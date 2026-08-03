#!/usr/bin/env python3
"""
fetch_travel_costs.py — extract trip COSTS from House gift-travel filing PDFs.

fetch_travel_pdf.py writes travel[] records from the Clerk's consolidated
search, which carries no dollar amounts — those exist only inside the filing
packet PDF (source_doc). Every packet contains a "SPONSOR POST-TRAVEL
DISCLOSURE FORM" whose Question 5 itemizes actual expenses:

    Total Transportation | Total Lodging | Total Meal | Total Other Expenses
    Traveler                    $a  $b  $c  $d (description)
    Accompanying Family Member  $a  $b  $c  $d

Two-stage extraction (measured on a 14-PDF sample spread 2023-2026):
  1. pdfplumber text layer — ~14% of packets have the typed values in text
     (sponsor filled the fillable PDF, or the Clerk's OCR caught them).
  2. OCR fallback — the form pages are clean typed fills in a ruled table
     (only signatures are handwritten), so tesseract handles the rest.
     OCR deps (tesseract binary + pytesseract + pypdfium2) are CI-only;
     when absent the doc is recorded 'ocr_unavailable' and retried in CI.

Scope: House packets only (disclosures-clerk.house.gov). Senate gift-rule
PDFs use a different form and are enormous multi-hundred-page scans — a
separate project if ever.

State: data/travel_cost_manifest.json maps source_doc URL -> outcome so
permanently valueless docs are never re-downloaded. total_cost is the SUM OF
THE TRAVELER ROW (the member's own benefit); the family row is stored
separately. total_cost == 0/empty means UNKNOWN, never "free trip".
"""

import json
import os
import re
import sys
import time
import tempfile
from datetime import datetime, timezone

import requests
import pdfplumber

DETAILS_DIR = "data/details"
MANIFEST_PATH = "data/travel_cost_manifest.json"

HOUSE_DOC_HOST = "disclosures-clerk.house.gov"

MAX_PDFS_PER_RUN = int(os.environ.get("MAX_PDFS_PER_RUN", "40"))
# 40/day: 1,038-doc backfill ≈ 26 daily runs; steady state is ~5-15 new/week
REQUEST_DELAY = 1.5        # seconds between downloads
MAX_OCR_PAGES = 6          # sponsor form was page 2 in every sampled packet
OCR_DPI = 300
MAX_SANE_AMOUNT = 200_000.0  # per-field sanity cap — reject OCR garbage

HEADERS = {
    "User-Agent": "CongressWatch/1.0 (public-interest-research; "
                  "mailto:project.congress.watch@gmail.com)",
    "Accept-Encoding": "gzip, deflate",
}

# Q5 markers. Text on these packets is often OCR output with collapsed
# spaces, so every multi-word marker tolerates optional whitespace.
START_RE = re.compile(r"Actual\s*amount\s*of\s*expenses", re.I)
END_RE = re.compile(r"All\s*expenses\s*connected\s*to\s*the\s*trip", re.I)
SPLIT_RE = re.compile(r"Accompanying", re.I)
DOLLAR_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{1,2})?)")

# Manifest statuses that must NOT be retried (re-downloading daily would
# hammer the Clerk for nothing). Transient ones (download_error,
# ocr_unavailable, pdf_error) are retried on later runs.
PERMANENT_STATUSES = {"costs_extracted", "no_values"}


class OcrUnavailable(Exception):
    pass


# ---------------------------------------------------------------------------
# IO helpers (repo conventions)
# ---------------------------------------------------------------------------

def load_json_strict(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    if not raw.strip():
        return default
    try:
        return json.loads(raw)
    except Exception as e:
        print(f"[FATAL] {path} exists but is not valid JSON ({e}). Aborting.")
        raise SystemExit(1)


def save_json(path, data):
    dirname = os.path.dirname(path)
    os.makedirs(dirname, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_costs_",
                                    suffix=".json", dir=dirname)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Q5 parsing (verified against sample packets; see module docstring)
# ---------------------------------------------------------------------------

def _amounts(text):
    out = []
    for raw in DOLLAR_RE.findall(text):
        try:
            v = float(raw.replace(",", ""))
        except ValueError:
            continue
        if 0 < v <= MAX_SANE_AMOUNT:
            out.append(v)
    return out


def parse_q5(page_text):
    """Parse the Q5 expense table out of one page's text.
    Returns dict or None if the Q5 region holds no dollar amounts."""
    m = START_RE.search(page_text)
    if not m:
        return None
    region = page_text[m.end():]
    e = END_RE.search(region)
    if e:
        region = region[: e.start()]

    s = SPLIT_RE.search(region)
    trav = _amounts(region[: s.start()] if s else region)
    fam = _amounts(region[s.start():] if s else "")
    if not trav and not fam:
        return None

    def rowmap(vals):
        return {
            "transportation": vals[0] if len(vals) > 0 else None,
            "lodging": vals[1] if len(vals) > 1 else None,
            "meals": vals[2] if len(vals) > 2 else None,
            "other": vals[3:],
        }

    return {
        "traveler_total": round(sum(trav), 2) if trav else None,
        "family_total": round(sum(fam), 2) if fam else None,
        "breakdown": rowmap(trav) if trav else None,
        "n_fields": len(trav),
    }


def extract_text_pass(path):
    """Stage 1: pdfplumber text layer. Returns (q5, saw_form_text)."""
    saw_form = False
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            if not page.chars:
                continue
            text = page.extract_text() or ""
            if START_RE.search(text):
                saw_form = True
                q5 = parse_q5(text)
                if q5:
                    return q5, True
                # Known limitation: multi-sponsor packets can carry a second
                # sponsor form; we take the first Q5 WITH values.
    return None, saw_form


def extract_ocr_pass(path):
    """Stage 2: render early pages and OCR them. CI-only deps; raises
    OcrUnavailable when the toolchain is absent (retried in CI later)."""
    try:
        import pytesseract
        import pypdfium2 as pdfium
        pytesseract.get_tesseract_version()
    except Exception as e:
        raise OcrUnavailable(str(e))

    pdf = pdfium.PdfDocument(path)
    try:
        for i in range(min(len(pdf), MAX_OCR_PAGES)):
            bitmap = pdf[i].render(scale=OCR_DPI / 72)
            text = pytesseract.image_to_string(bitmap.to_pil())
            if START_RE.search(text):
                q5 = parse_q5(text)
                if q5:
                    return q5
    finally:
        pdf.close()
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def collect_candidates(manifest):
    """(detail_path, record_index, source_doc) for every House travel record
    still missing a cost and not permanently settled in the manifest."""
    cands = []
    for fname in sorted(os.listdir(DETAILS_DIR)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(DETAILS_DIR, fname)
        detail = load_json_strict(path, {})
        for idx, t in enumerate(detail.get("travel") or []):
            url = t.get("source_doc") or ""
            if HOUSE_DOC_HOST not in url:
                continue
            if t.get("total_cost"):
                continue
            if manifest.get(url, {}).get("status") in PERMANENT_STATUSES:
                continue
            cands.append((path, idx, url))
    return cands


def main():
    print("=" * 60)
    print("HOUSE TRAVEL COST EXTRACTOR")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    manifest = load_json_strict(MANIFEST_PATH, {})
    cands = collect_candidates(manifest)
    print(f"Travel records missing costs (retryable): {len(cands)}")
    if not cands:
        print("Backfill complete — nothing to do.")
        return

    batch = cands[:MAX_PDFS_PER_RUN]
    stats = {"extracted_text": 0, "extracted_ocr": 0, "no_values": 0,
             "ocr_unavailable": 0, "download_error": 0, "pdf_error": 0}
    now = datetime.now(timezone.utc).isoformat()

    for n, (detail_path, idx, url) in enumerate(batch):
        if n > 0:
            time.sleep(REQUEST_DELAY)
        entry = manifest.setdefault(url, {"tries": 0})
        entry["tries"] = entry.get("tries", 0) + 1
        entry["last_attempt"] = now
        tag = f"[{n + 1}/{len(batch)}] {os.path.basename(url)}"

        tmp = None
        try:
            r = requests.get(url, headers=HEADERS, timeout=90, stream=True)
            if r.status_code != 200:
                entry["status"] = "download_error"
                stats["download_error"] += 1
                print(f"{tag}: HTTP {r.status_code}")
                continue
            fd, tmp = tempfile.mkstemp(suffix=".pdf")
            with os.fdopen(fd, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)

            try:
                q5, saw_form = extract_text_pass(tmp)
                method = "text"
                if q5 is None:
                    q5 = extract_ocr_pass(tmp)
                    method = "ocr"
            except OcrUnavailable:
                entry["status"] = "ocr_unavailable"
                stats["ocr_unavailable"] += 1
                print(f"{tag}: text layer empty, OCR toolchain absent "
                      f"(will retry where installed)")
                continue
            except Exception as e:
                entry["status"] = "pdf_error"
                stats["pdf_error"] += 1
                print(f"{tag}: PDF processing failed: {e}")
                continue

            if q5 is None or not q5.get("traveler_total"):
                # Both passes ran and found no traveler amounts — permanent.
                entry["status"] = "no_values"
                stats["no_values"] += 1
                print(f"{tag}: no traveler amounts found (form blank or "
                      f"handwritten)")
                continue

            # Write back into the exact travel record, matched by source_doc.
            detail = load_json_strict(detail_path, {})
            travel = detail.get("travel") or []
            rec = travel[idx] if idx < len(travel) else None
            if not rec or rec.get("source_doc") != url:
                rec = next((t for t in travel
                            if t.get("source_doc") == url), None)
            if rec is None:
                entry["status"] = "pdf_error"
                stats["pdf_error"] += 1
                print(f"{tag}: travel record vanished from {detail_path}")
                continue

            rec["total_cost"] = q5["traveler_total"]
            if q5.get("family_total"):
                rec["family_cost"] = q5["family_total"]
            rec["cost_breakdown"] = q5.get("breakdown")
            rec["cost_extraction"] = {"method": method,
                                      "n_fields": q5.get("n_fields"),
                                      "extracted_at": now}
            save_json(detail_path, detail)

            entry["status"] = "costs_extracted"
            entry["method"] = method
            stats[f"extracted_{method}"] += 1
            print(f"{tag}: ${q5['traveler_total']:,.2f} via {method} "
                  f"({q5.get('n_fields')} fields)")
        except requests.RequestException as e:
            entry["status"] = "download_error"
            stats["download_error"] += 1
            print(f"{tag}: download failed: {e}")
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)

    save_json(MANIFEST_PATH, manifest)

    print("=" * 60)
    print("TRAVEL COSTS — COMPLETE")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"Finished: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    # Every single download failing means the Clerk is blocking us or the
    # URL scheme changed — fail the run so CI flags it.
    attempted = len(batch)
    if attempted and stats["download_error"] == attempted:
        print("[FATAL] all downloads failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
