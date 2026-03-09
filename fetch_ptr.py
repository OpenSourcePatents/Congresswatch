"""
CongressWatch — PTR / Congressional Trade Fetcher (Production v1)

Purpose:
• Pull Periodic Transaction Report (PTR) PDFs from official source URLs
• Parse trade rows heuristically from PDF text
• Write deep trade data into data/details/{bioguideId}.json
• Keep only lightweight summary fields in data/members.json

Input:
• data/members.json
• data/ptr_source_manifest.json

Manifest format:
[
  {
    "bioguide_id": "P000197",
    "name": "Nancy Pelosi",
    "chamber": "House",
    "source_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20033725.pdf",
    "report_id": "20033725",
    "filed_date": "2026-01-23",
    "source_system": "house_clerk"
  }
]

Notes:
• This version is source-URL driven on purpose.
• It fits the same split-file architecture as fetch_finance.py.
• It uses safe merge logic so it won’t wipe vote / finance / bill fields.
"""

import os
import re
import io
import json
import time
import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

# PDF reader fallback
PDF_BACKEND = None
try:
    from pypdf import PdfReader  # type: ignore
    PDF_BACKEND = "pypdf"
except Exception:
    try:
        from PyPDF2 import PdfReader  # type: ignore
        PDF_BACKEND = "PyPDF2"
    except Exception:
        PdfReader = None
        PDF_BACKEND = None


HEADERS = {
    "User-Agent": "CongressWatch/1.0 (public-interest-research; mailto:project.congress.watch@gmail.com)",
    "Accept-Encoding": "gzip, deflate",
}

OUTPUT_FILE = "data/members.json"
DETAILS_DIR = "data/details"
CACHE_DIR = "data/cache"
PTR_CACHE_DIR = os.path.join(CACHE_DIR, "ptr_pdfs")
PTR_MANIFEST_FILE = "data/ptr_source_manifest.json"

os.makedirs(DETAILS_DIR, exist_ok=True)
os.makedirs(PTR_CACHE_DIR, exist_ok=True)

LIGHT_PTR_FIELDS = {
    "ptr_trade_count",
    "ptr_last_trade_date",
    "ptr_flags",
    "congressional_trade_signals",
    "ptr_data_updated",
}

DATE_PATTERNS = [
    "%m/%d/%Y",
    "%m/%d/%y",
    "%Y-%m-%d",
    "%B %d, %Y",
    "%b %d, %Y",
]

TRADE_TYPE_MAP = {
    "P": "purchase",
    "S": "sale",
    "E": "exchange",
    "purchase": "purchase",
    "purchased": "purchase",
    "sale": "sale",
    "sold": "sale",
    "exchange": "exchange",
    "exchanged": "exchange",
}

AMOUNT_RANGE_RE = re.compile(
    r"\$(\d[\d,]*)\s*(?:-|to|–|—)\s*\$(\d[\d,]*)",
    flags=re.I
)

DATE_RE = re.compile(
    r"\b(?:\d{1,2}/\d{1,2}/\d{2,4}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|"
    r"January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},\s+\d{4}|"
    r"\d{4}-\d{2}-\d{2})\b"
)

TICKER_PAREN_RE = re.compile(r"\(([A-Z]{1,6})\)")
TICKER_LABEL_RE = re.compile(r"\bTicker[:\s]+([A-Z]{1,6})\b")
TYPE_RE = re.compile(r"\b(Purchase|Purchased|Sale|Sold|Exchange|Exchanged|P|S|E)\b", flags=re.I)

# Common PTR amount buckets
KNOWN_MIN_BUCKETS = [
    1001, 15001, 50001, 100001, 250001, 500001, 1000001, 5000001, 25000001, 50000001
]


def sleep(seconds: float = 1.1) -> None:
    time.sleep(seconds)


def load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, payload: Any) -> None:
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)


def load_members() -> List[Dict[str, Any]]:
    try:
        with open(OUTPUT_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Critical Error: Could not load {OUTPUT_FILE}: {e}")
        return []


def load_detail(bid: str) -> Dict[str, Any]:
    path = os.path.join(DETAILS_DIR, f"{bid}.json")
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_detail(bid: str, data: Dict[str, Any]) -> None:
    path = os.path.join(DETAILS_DIR, f"{bid}.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def normalize_name(name: str) -> str:
    name = (name or "").lower().strip()
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", name)
    name = re.sub(r"[^a-z\s'-]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def parse_date(date_str: Optional[str]) -> Optional[str]:
    if not date_str:
        return None
    raw = str(date_str).strip()
    for fmt in DATE_PATTERNS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except Exception:
            continue
    return None


def parse_amount_range(text: str) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    if not text:
        return None, None, None

    m = AMOUNT_RANGE_RE.search(text)
    if m:
        lo = int(m.group(1).replace(",", ""))
        hi = int(m.group(2).replace(",", ""))
        return lo, hi, f"${lo:,}-${hi:,}"

    # Handle single-ended ranges if they appear
    gte = re.search(r"\$?(\d[\d,]*)\s*\+", text)
    if gte:
        lo = int(gte.group(1).replace(",", ""))
        return lo, None, f"${lo:,}+"

    return None, None, None


def extract_pdf_text(pdf_bytes: bytes) -> str:
    if PdfReader is None:
        raise RuntimeError("No PDF parser found. Install pypdf or PyPDF2.")

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        parts = []
        for page in reader.pages:
            try:
                txt = page.extract_text() or ""
            except Exception:
                txt = ""
            if txt:
                parts.append(txt)
        return "\n".join(parts)
    except Exception as e:
        raise RuntimeError(f"PDF parse failed: {e}")


def download_pdf(url: str) -> bytes:
    sleep(1.0)
    r = requests.get(url, headers=HEADERS, timeout=45)
    r.raise_for_status()
    return r.content


def cache_key_for_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def get_pdf_bytes(url: str) -> bytes:
    key = cache_key_for_url(url)
    path = os.path.join(PTR_CACHE_DIR, f"{key}.pdf")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()

    content = download_pdf(url)
    with open(path, "wb") as f:
        f.write(content)
    return content


def extract_ticker(text: str) -> Optional[str]:
    if not text:
        return None
    m = TICKER_LABEL_RE.search(text)
    if m:
        return m.group(1).upper()
    m = TICKER_PAREN_RE.search(text)
    if m:
        return m.group(1).upper()
    return None


def extract_trade_type(text: str) -> Optional[str]:
    if not text:
        return None
    m = TYPE_RE.search(text)
    if not m:
        return None
    raw = m.group(1).strip()
    return TRADE_TYPE_MAP.get(raw.lower()) or TRADE_TYPE_MAP.get(raw.upper())


def collect_candidate_trade_chunks(lines: List[str], window: int = 3) -> List[str]:
    """
    Build overlapping text windows to catch table rows split across lines.
    """
    chunks = []
    clean = [re.sub(r"\s+", " ", x).strip() for x in lines if x and x.strip()]
    for i in range(len(clean)):
        for span in range(1, window + 1):
            if i + span <= len(clean):
                chunk = " | ".join(clean[i:i + span])
                if DATE_RE.search(chunk) and TYPE_RE.search(chunk):
                    chunks.append(chunk)
    # de-dup preserving order
    seen = set()
    out = []
    for c in chunks:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def best_asset_name_from_chunk(chunk: str) -> str:
    """
    Very heuristic: keep the leftmost meaningful text before obvious fields.
    """
    text = chunk.replace("|", " ").strip()

    # Split before known trade markers or dates
    splitters = [
        r"\bPurchase\b", r"\bPurchased\b", r"\bSale\b", r"\bSold\b", r"\bExchange\b", r"\bExchanged\b",
        r"\bP\b", r"\bS\b", r"\bE\b"
    ]
    for pattern in splitters:
        m = re.search(pattern, text, flags=re.I)
        if m and m.start() > 0:
            candidate = text[:m.start()].strip(" -:;,.")
            if len(candidate) >= 3:
                return candidate

    # Fallback: strip dates and amounts from whole chunk
    candidate = DATE_RE.sub("", text)
    candidate = AMOUNT_RANGE_RE.sub("", candidate)
    candidate = re.sub(r"\b(Purchase|Purchased|Sale|Sold|Exchange|Exchanged|P|S|E)\b", "", candidate, flags=re.I)
    candidate = re.sub(r"\s+", " ", candidate).strip(" -:;,.")
    return candidate[:160]


def parse_trade_chunks(pdf_text: str) -> List[Dict[str, Any]]:
    lines = pdf_text.splitlines()
    chunks = collect_candidate_trade_chunks(lines, window=3)

    trades = []
    seen = set()

    for chunk in chunks:
        trade_type = extract_trade_type(chunk)
        if not trade_type:
            continue

        date_match = DATE_RE.search(chunk)
        tx_date = parse_date(date_match.group(0)) if date_match else None

        amount_min, amount_max, amount_label = parse_amount_range(chunk)
        ticker = extract_ticker(chunk)
        asset_name = best_asset_name_from_chunk(chunk)

        # Skip junk
        if not tx_date and amount_min is None and not ticker:
            continue
        if len(asset_name) < 2:
            asset_name = "Unknown Asset"

        key = (asset_name, ticker, trade_type, tx_date, amount_label)
        if key in seen:
            continue
        seen.add(key)

        trades.append({
            "asset_name": asset_name,
            "ticker": ticker,
            "transaction_type": trade_type,
            "transaction_date": tx_date,
            "amount_range": amount_label,
            "amount_min": amount_min,
            "amount_max": amount_max,
            "owner": None,
            "raw_excerpt": chunk[:500],
        })

    return trades


def summarize_trades(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    buy_min = buy_max = sell_min = sell_max = 0
    last_trade_date = None
    symbols = set()

    for t in trades:
        ttype = t.get("transaction_type")
        lo = t.get("amount_min") or 0
        hi = t.get("amount_max") or 0

        if ttype == "purchase":
            buy_min += lo
            buy_max += hi
        elif ttype == "sale":
            sell_min += lo
            sell_max += hi

        dt = t.get("transaction_date")
        if dt and (last_trade_date is None or dt > last_trade_date):
            last_trade_date = dt

        ticker = t.get("ticker")
        if ticker:
            symbols.add(ticker)

    flags = []
    trade_count = len(trades)
    if trade_count >= 10:
        flags.append("heavy_trader")
    if sell_max >= 1_000_000 or buy_max >= 1_000_000:
        flags.append("large_volume")
    if any(t.get("transaction_type") == "exchange" for t in trades):
        flags.append("exchange_activity")

    return {
        "ptr_trade_count": trade_count,
        "congressional_trade_signals": trade_count,
        "ptr_last_trade_date": last_trade_date,
        "ptr_buy_volume_min": buy_min,
        "ptr_buy_volume_max": buy_max,
        "ptr_sell_volume_min": sell_min,
        "ptr_sell_volume_max": sell_max,
        "ptr_symbols": sorted(symbols),
        "ptr_flags": flags,
    }


def days_between(a: Optional[str], b: Optional[str]) -> Optional[int]:
    try:
        if not a or not b:
            return None
        da = datetime.fromisoformat(a).date()
        db = datetime.fromisoformat(b).date()
        return abs((db - da).days)
    except Exception:
        return None


def enrich_filing_with_lateness(filing: Dict[str, Any], trades: List[Dict[str, Any]]) -> None:
    filed = parse_date(filing.get("filed_date"))
    filing["filed_date"] = filed
    filing["late_trade_count"] = 0

    if not filed:
        return

    for t in trades:
        tdate = t.get("transaction_date")
        lag = days_between(tdate, filed)
        t["days_to_file"] = lag
        if lag is not None and lag > 45:
            t["late_filing_flag"] = True
            filing["late_trade_count"] += 1
        else:
            t["late_filing_flag"] = False


def resolve_member_from_manifest_entry(
    entry: Dict[str, Any],
    member_index: Dict[str, Dict[str, Any]],
    member_name_index: Dict[str, str],
) -> Optional[str]:
    bid = entry.get("bioguide_id") or entry.get("id")
    if bid and bid in member_index:
        return bid

    name = normalize_name(entry.get("name", ""))
    if name and name in member_name_index:
        return member_name_index[name]

    return None


def build_member_indexes(members: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    by_bid = {}
    by_name = {}

    for m in members:
        bid = m.get("id") or m.get("bioguide_id")
        if not bid:
            continue
        by_bid[bid] = m
        nm = normalize_name(m.get("name", ""))
        if nm:
            by_name[nm] = bid

    return by_bid, by_name


def upsert_light_fields(member: Dict[str, Any], ptr_summary: Dict[str, Any]) -> None:
    for k in LIGHT_PTR_FIELDS:
        if k in ptr_summary:
            member[k] = ptr_summary[k]


def process_manifest_entry(
    entry: Dict[str, Any],
    member_index: Dict[str, Dict[str, Any]],
    member_name_index: Dict[str, str],
) -> Optional[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
    bid = resolve_member_from_manifest_entry(entry, member_index, member_name_index)
    if not bid:
        print(f"  PTR manifest skip: could not match member for entry {entry.get('name') or entry.get('source_url')}")
        return None

    source_url = entry.get("source_url")
    if not source_url:
        print(f"  PTR manifest skip: missing source_url for {bid}")
        return None

    try:
        pdf_bytes = get_pdf_bytes(source_url)
        pdf_text = extract_pdf_text(pdf_bytes)
        trades = parse_trade_chunks(pdf_text)

        filing = {
            "report_id": entry.get("report_id"),
            "source_url": source_url,
            "source_system": entry.get("source_system") or entry.get("chamber", "").lower(),
            "chamber": entry.get("chamber"),
            "filed_date": entry.get("filed_date"),
            "member_name": entry.get("name"),
            "trade_count": len(trades),
            "parsed_at": datetime.now().isoformat(),
        }

        enrich_filing_with_lateness(filing, trades)

        # attach filing metadata onto each trade
        for t in trades:
            t["report_id"] = filing.get("report_id")
            t["source_url"] = source_url
            t["source_system"] = filing.get("source_system")
            t["chamber"] = filing.get("chamber")
            t["filed_date"] = filing.get("filed_date")

        summary = summarize_trades(trades)
        return bid, filing, {"ptr_filings": [filing], "ptr_trades": trades, **summary}

    except Exception as e:
        print(f"  PTR parse failed for {bid}: {e}")
        return None


def merge_ptr_payload(existing: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    # Start with prior lists
    filings = existing.get("ptr_filings", []) or []
    trades = existing.get("ptr_trades", []) or []

    # Append new filings/trades if not duplicates
    existing_filing_keys = {
        (f.get("report_id"), f.get("source_url")) for f in filings if isinstance(f, dict)
    }
    for filing in payload.get("ptr_filings", []) or []:
        key = (filing.get("report_id"), filing.get("source_url"))
        if key not in existing_filing_keys:
            filings.append(filing)

    existing_trade_keys = {
        (
            t.get("report_id"),
            t.get("asset_name"),
            t.get("ticker"),
            t.get("transaction_type"),
            t.get("transaction_date"),
            t.get("amount_range"),
        )
        for t in trades if isinstance(t, dict)
    }

    for t in payload.get("ptr_trades", []) or []:
        key = (
            t.get("report_id"),
            t.get("asset_name"),
            t.get("ticker"),
            t.get("transaction_type"),
            t.get("transaction_date"),
            t.get("amount_range"),
        )
        if key not in existing_trade_keys:
            trades.append(t)

    # Recompute summary from merged trade list
    summary = summarize_trades(trades)

    out = dict(existing)
    out["ptr_filings"] = filings
    out["ptr_trades"] = trades
    out.update(summary)
    out["ptr_data_updated"] = datetime.now().isoformat()

    # late-filer rollup
    all_flags = set(out.get("ptr_flags", []) or [])
    if any(t.get("late_filing_flag") for t in trades):
        all_flags.add("late_filer")
    out["ptr_flags"] = sorted(all_flags)

    return out


if __name__ == "__main__":
    members = load_members()
    if not members:
        raise SystemExit(1)

    manifest = load_json(PTR_MANIFEST_FILE, [])
    if not manifest:
        print(f"No manifest entries found in {PTR_MANIFEST_FILE}")
        print("Create that file, then rerun.")
        raise SystemExit(1)

    member_index, member_name_index = build_member_indexes(members)

    print(f"Starting PTR run: {len(manifest)} source entries")
    touched = set()

    # Process manifest
    for i, entry in enumerate(manifest, start=1):
        label = entry.get("name") or entry.get("bioguide_id") or entry.get("source_url")
        print(f"[{i}/{len(manifest)}] {label}")

        result = process_manifest_entry(entry, member_index, member_name_index)
        if not result:
            continue

        bid, filing, payload = result
        touched.add(bid)

        detail_data = load_detail(bid)
        detail_data = merge_ptr_payload(detail_data, payload)
        detail_data["last_updated"] = detail_data["ptr_data_updated"]
        save_detail(bid, detail_data)

        # Also update the in-memory member row with only light PTR summary fields
        member_row = member_index.get(bid, {})
        upsert_light_fields(member_row, detail_data)
        member_row["data_updated"] = detail_data["ptr_data_updated"]

    # Rebuild members.json preserving all existing member rows + new light PTR fields
    save_json(OUTPUT_FILE, members)

    print(f"\n✓ PTR run complete.")
    print(f"  touched members: {len(touched)}")
    print(f"  detail files updated: {len(touched)}")
    print(f"  members.json refreshed: {OUTPUT_FILE}")