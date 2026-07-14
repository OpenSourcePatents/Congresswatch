#!/usr/bin/env python3
"""
fetch_house_trades.py — House Financial Disclosure PTR Scraper
===============================================================
Pulls House member Periodic Transaction Reports (PTRs) from the
House Clerk financial disclosure system.

How it works (rebuilt 2026-07):
  1. Downloads the Clerk's yearly financial-disclosure index ZIP:
       https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip
     which contains {year}FD.xml listing every filing
     (Last, First, Suffix, FilingType, StateDst, Year, FilingDate, DocID).
     FilingType "P" = Periodic Transaction Report.
  2. Matches each PTR filing to a House member in data/members.json by
     last name + state/district (StateDst e.g. "CA12"). Ambiguous or
     unmatchable filings are SKIPPED and counted — never guessed.
  3. Downloads each new PTR PDF:
       https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{DocID}.pdf
     and extracts the transactions table with pdfplumber.
     Scanned paper filings (DocID starting with 8/9, or no extractable
     text) are skipped gracefully and counted in stats.

Output:
  - data/details/{bioguide_id}.json — trades[] array (safe merge)
  - data/members.json — trade_count + latest_trade_date

Env vars (optional):
  MAX_NEW_FILINGS   — cap on new PDFs parsed per run (default 150)
  DRY_RUN=1         — parse everything but write nothing

Run:
  pip install requests pdfplumber
  python fetch_house_trades.py [--dry-run]
"""

import io
import json
import os
import re
import sys
import time
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
import requests
import pdfplumber
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
MEMBERS_PATH = os.path.join(BASE_DIR, "data", "members.json")
DETAILS_DIR  = os.path.join(BASE_DIR, "data", "details")

DISCLOSURES_BASE  = "https://disclosures-clerk.house.gov"
DISCLOSURES_INDEX = f"{DISCLOSURES_BASE}/public_disc/financial-pdfs/{{year}}FD.zip"
DISCLOSURES_PTR   = f"{DISCLOSURES_BASE}/public_disc/ptr-pdfs"

USER_AGENT    = ("CongressWatch/1.0 "
                 "(public-interest-research; "
                 "mailto:project.congress.watch@gmail.com)")
REQUEST_DELAY = 1.5   # seconds between PDF downloads
MAX_RETRIES   = 3
BACKOFF_BASE  = 3     # seconds, doubles each retry

MAX_NEW_FILINGS = int(os.environ.get("MAX_NEW_FILINGS", "150"))
DRY_RUN = (os.environ.get("DRY_RUN", "") == "1"
           or "--dry-run" in sys.argv)

# Full state/territory name (as used in members.json) -> USPS abbreviation
STATE_ABBREV = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO",
    "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "puerto rico": "PR",
    "guam": "GU", "virgin islands": "VI", "u.s. virgin islands": "VI",
    "american samoa": "AS", "northern mariana islands": "MP",
}

# House PTR transaction-type codes -> readable labels
TYPE_MAP = {
    "P": "Purchase",
    "S": "Sale",
    "S (partial)": "Sale (Partial)",
    "P (partial)": "Purchase (Partial)",
    "E": "Exchange",
    "E (partial)": "Exchange (Partial)",
}

# House PTR owner codes -> readable labels
OWNER_MAP = {
    "": "Self",
    "SP": "Spouse",
    "DC": "Dependent Child",
    "JT": "Joint",
}

# Common House asset-type codes (from fd.house.gov/reference/asset-type-codes)
# Unknown codes pass through as-is.
ASSET_TYPE_MAP = {
    "ST": "Stock",
    "MF": "Mutual Fund",
    "EF": "Exchange Traded Fund",
    "ET": "Exchange Traded Note",
    "OP": "Options",
    "OT": "Other Securities",
    "GS": "Government Securities",
    "CS": "Corporate Bond",
    "CT": "Cryptocurrency",
    "PS": "Private Company Stock",
    "RS": "Restricted Stock",
    "RE": "REIT",
    "RP": "Real Property",
    "FU": "Futures",
    "FE": "Foreign Exchange Position",
    "HE": "Hedge Fund / Private Equity",
    "VA": "Variable Annuity",
    "FN": "Fixed Annuity",
}


# ---------------------------------------------------------------------------
# JSON helpers (X1 fix: atomic writes, abort on corrupt existing files)
# ---------------------------------------------------------------------------

def load_json(path, default):
    """
    Load JSON from path.

    Missing or empty files return `default`. An EXISTING, NON-EMPTY file
    that fails to parse ABORTS the run — returning {} and later saving
    would silently wipe data written by other pipelines (votes, finance,
    bills).
    """
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        print(f"[FATAL] Cannot read {path}: {e}")
        raise SystemExit(1)
    if not content.strip():
        return default
    try:
        return json.loads(content)
    except Exception as e:
        print(f"[FATAL] Existing file {path} is not valid JSON ({e}).")
        print("[FATAL] Aborting so we don't overwrite another pipeline's data.")
        raise SystemExit(1)


def save_json(path, data):
    """Atomic write: dump to a temp file, then os.replace over the target."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Name / date helpers
# ---------------------------------------------------------------------------

def normalize_name(name):
    """Transliterate accents (Sánchez -> sanchez), lowercase,
    strip suffixes and non-alpha."""
    name = unicodedata.normalize("NFKD", name or "")
    name = name.encode("ascii", "ignore").decode("ascii")
    name = name.lower().strip()
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v|hon|rep|dr|mr|mrs|ms)\b\.?", "", name)
    name = re.sub(r"[^a-z\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def parse_date_any(s):
    """Parse MM/DD/YYYY or YYYY-MM-DD into a datetime, else None."""
    s = (s or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def to_iso_date(s):
    """MM/DD/YYYY -> YYYY-MM-DD (returns input unchanged if unparseable)."""
    dt = parse_date_any(s)
    return dt.strftime("%Y-%m-%d") if dt else s


def latest_iso_date(trades):
    """
    H3 fix: compute the latest transaction date by PARSING dates
    (handles legacy MM/DD/YYYY and new ISO YYYY-MM-DD), not by
    lexicographic string max(). Returns ISO string or "".
    """
    best = None
    for t in trades:
        dt = parse_date_any(t.get("transaction_date", ""))
        if dt and (best is None or dt > best):
            best = dt
    return best.strftime("%Y-%m-%d") if best else ""


def trade_key(t):
    """Dedup key: DocID (via ptr_link) + date + asset, plus type/amount
    so a same-day buy and sell of the same asset stay distinct."""
    return (
        t.get("ptr_link", ""),
        t.get("transaction_date", ""),
        normalize_name(re.sub(r"<[^>]+>", "", t.get("asset_description", ""))),
        t.get("ticker", ""),
        t.get("type", ""),
        t.get("amount", ""),
    )


# ---------------------------------------------------------------------------
# HTTP with retry/backoff
# ---------------------------------------------------------------------------

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})


def http_get(url, timeout=60):
    """GET with retry + exponential backoff on 429/5xx. Returns Response or None."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = SESSION.get(url, timeout=timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = BACKOFF_BASE * (2 ** attempt)
                print(f"    HTTP {resp.status_code} on {url}, retrying in {wait}s...")
                time.sleep(wait)
                continue
            return resp
        except requests.exceptions.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                wait = BACKOFF_BASE * (2 ** attempt)
                print(f"    Request error: {e}, retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(f"    Request failed permanently: {e}")
    return None


# ---------------------------------------------------------------------------
# FD index (yearly ZIP -> XML -> PTR filing list)
# ---------------------------------------------------------------------------

def fetch_fd_index(year):
    """
    Download and parse {year}FD.zip from the Clerk.
    Returns a list of PTR filing dicts, or None on download/parse failure.
    """
    url = DISCLOSURES_INDEX.format(year=year)
    print(f"[INDEX] Downloading {url}")
    resp = http_get(url)
    if resp is None or resp.status_code != 200:
        print(f"[INDEX] Failed: HTTP "
              f"{resp.status_code if resp is not None else 'no response'}")
        return None

    try:
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        xml_name = next(n for n in zf.namelist()
                        if n.lower().endswith(".xml"))
        with zf.open(xml_name) as f:
            root = ET.parse(f).getroot()
    except Exception as e:
        print(f"[INDEX] Could not parse {year}FD.zip: {e}")
        return None

    filings = []
    for member in root:
        if (member.findtext("FilingType") or "").strip() != "P":
            continue
        doc_id = (member.findtext("DocID") or "").strip()
        if not doc_id:
            continue
        filing_year = (member.findtext("Year") or "").strip() or str(year)
        filings.append({
            "last": (member.findtext("Last") or "").strip(),
            "first": (member.findtext("First") or "").strip(),
            "suffix": (member.findtext("Suffix") or "").strip(),
            "state_dst": (member.findtext("StateDst") or "").strip(),
            "filing_date": (member.findtext("FilingDate") or "").strip(),
            "doc_id": doc_id,
            "year": filing_year,
            "pdf_url": f"{DISCLOSURES_PTR}/{filing_year}/{doc_id}.pdf",
        })

    print(f"[INDEX] {year}: {len(filings)} PTR filings listed")
    return filings


# ---------------------------------------------------------------------------
# Member matching (H2/H4 fix: match on last name + state/district,
# never blind-fallback to the first same-surname member)
# ---------------------------------------------------------------------------

def build_member_indexes(house_members):
    """Returns (district_index, state_index):
    (state_abbrev, district_int) -> [members], state_abbrev -> [members]."""
    district_index = {}
    state_index = {}
    for m in house_members:
        abbrev = STATE_ABBREV.get((m.get("state") or "").strip().lower())
        if not abbrev:
            continue
        dist = (m.get("district") or "").strip()
        dist_num = int(dist) if dist.isdigit() else 0  # at-large -> 0
        district_index.setdefault((abbrev, dist_num), []).append(m)
        state_index.setdefault(abbrev, []).append(m)
    return district_index, state_index


def last_name_compatible(fd_last, member_name):
    """True if the filing's last name plausibly belongs to this member."""
    fd_norm = normalize_name(fd_last)
    mem_norm = normalize_name(member_name)
    if not fd_norm or not mem_norm:
        return False
    if mem_norm == fd_norm or mem_norm.endswith(" " + fd_norm):
        return True
    # Hyphenated / multi-word surname: last tokens agree
    return mem_norm.split()[-1] == fd_norm.split()[-1]


def _pick_by_first_name(filing, candidates):
    """Disambiguate same-surname candidates by first name; None if unclear."""
    fd_first = normalize_name(filing["first"]).split()
    hits = []
    for m in candidates:
        mem_first = normalize_name(m.get("name", "")).split()
        if fd_first and mem_first and fd_first[0] == mem_first[0]:
            hits.append(m)
    return hits[0] if len(hits) == 1 else None


def match_filing_to_member(filing, district_index, state_index):
    """
    Match an FD.xml filing to a members.json House member using
    StateDst (e.g. "CA12") + last name. Returns member dict or None.

    Falls back to a UNIQUE statewide last-name match when the district
    doesn't line up (redistricting drift between FD.xml and members.json).
    Never guesses: ambiguous filings return None and are skipped.
    """
    sd = filing["state_dst"]
    if len(sd) < 3 or not sd[:2].isalpha() or not sd[2:].isdigit():
        return None
    state = sd[:2].upper()
    key = (state, int(sd[2:]))

    candidates = [m for m in district_index.get(key, [])
                  if last_name_compatible(filing["last"], m.get("name", ""))]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        return _pick_by_first_name(filing, candidates)

    # District miss: unique statewide surname match is still confident.
    statewide = [m for m in state_index.get(state, [])
                 if last_name_compatible(filing["last"], m.get("name", ""))]
    if len(statewide) == 1:
        return statewide[0]
    if len(statewide) > 1:
        return _pick_by_first_name(filing, statewide)

    # No confident match -> skip (counted by caller), never guess.
    return None


# ---------------------------------------------------------------------------
# PTR PDF parser
# ---------------------------------------------------------------------------

# A transaction row's first line: [owner] asset... type date notif-date amount
TXN_LINE_RE = re.compile(
    r"^(?:(?P<owner>SP|DC|JT)\s+)?"
    r"(?P<asset>.*?)\s*"
    r"(?P<type>[PSE](?:\s*\(partial\))?)\s+"
    r"(?P<tdate>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<ndate>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<amount>\$[\d,]+(?:\s*-\s*\$[\d,]+)?|\$[\d,]+\s*-|"
    r"Over\s+\$[\d,]+|\$[\d,]+\s*\+)"
    # Tolerate a trailing cap-gains checkbox glyph, but never swallow a
    # dangling range dash or amount digits (wrapped ranges end in "-").
    r"(?:\s+[^\s$\d,-]{1,2})?\s*$"
)

# Sub-row metadata labels. Small-caps labels extract with wide gaps,
# e.g. "F      S     : New" == "Filing Status: New".
META_LABEL_RES = [
    ("filing_status", re.compile(r"^F(?:iling)?\s+S(?:tatus)?\s*:\s*(.*)$")),
    ("subholding",    re.compile(r"^S(?:ubholding)?\s+O(?:f)?\s*:\s*(.*)$")),
    ("description",   re.compile(r"^D(?:escription)?\s+:\s*(.*)$")),
    ("location",      re.compile(r"^L(?:ocation)?\s+:\s*(.*)$")),
    ("comments",      re.compile(r"^C(?:omments?)?\s+:\s*(.*)$")),
]

TABLE_HEADER_PREFIXES = (
    "ID Owner Asset",
    "Type Date",
    "$200?",
)
TABLE_END_PREFIX = "* For the complete list"

TICKER_RE = re.compile(r"\(([A-Z][A-Z0-9./\-]{0,11})\)")
EXCHANGE_TICKER_RE = re.compile(
    r"\b(?:NYSEARCA|NASDAQ|NYSE|BATS|AMEX|OTC)\s*:\s*([A-Z][A-Z0-9./\-]{0,11})")
ASSET_CODE_RE = re.compile(r"\[([A-Z0-9]{2,3})\]\s*$")
AMOUNT_TAIL_RE = re.compile(r"^(.*?)\s*(\$[\d,]+)\s*$")

MAX_COMMENT_LEN = 600


def _finalize_trade(raw, ptr_url):
    """Turn accumulated raw row parts into a trade record (senate schema)."""
    asset = re.sub(r"\s+", " ", raw["asset"]).strip()

    asset_type = ""
    m = ASSET_CODE_RE.search(asset)
    if m:
        code = m.group(1)
        asset_type = ASSET_TYPE_MAP.get(code, code)
        asset = asset[:m.start()].strip()

    ticker = "--"
    tickers = TICKER_RE.findall(asset)
    if tickers:
        ticker = tickers[-1]
    else:
        m = EXCHANGE_TICKER_RE.search(asset)
        if m:
            ticker = m.group(1)

    amount = re.sub(r"\s+", " ", raw["amount"]).strip()
    amount = re.sub(r"\s*-\s*", " - ", amount).strip()
    # Unterminated range (continuation never found) -> strip dangling dash
    amount = amount.rstrip("-").strip()

    comment = re.sub(r"\s+", " ", raw["comment"]).strip()
    if len(comment) > MAX_COMMENT_LEN:
        comment = comment[:MAX_COMMENT_LEN].rstrip() + "..."

    txn_type = re.sub(r"\s+", " ", raw["type"]).strip()

    return {
        "transaction_date": to_iso_date(raw["tdate"]),
        "owner": OWNER_MAP.get(raw["owner"], raw["owner"] or "--"),
        "ticker": ticker,
        "asset_description": asset,
        "asset_type": asset_type,
        "type": TYPE_MAP.get(txn_type, txn_type),
        "amount": amount,
        "comment": comment or "--",
        "ptr_link": ptr_url,
    }


def parse_ptr_pdf(pdf_bytes, ptr_url=""):
    """
    Parse an electronic House PTR PDF into a list of trade dicts.

    Returns (trades, has_text):
      has_text False  -> scanned paper filing (no extractable text)
      trades == []    -> nothing parsed (empty or unexpected layout)
    """
    lines = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines.extend(text.replace("\x00", " ").split("\n"))

    if not any(ln.strip() for ln in lines):
        return [], False

    trades = []
    raw = None          # accumulating row parts
    mode = "seek"       # seek -> (asset | meta | desc)
    in_table = False

    def flush():
        nonlocal raw
        if raw is not None:
            trades.append(_finalize_trade(raw, ptr_url))
            raw = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if not in_table:
            if line.startswith(TABLE_HEADER_PREFIXES[0]):
                in_table = True
            continue

        if line.startswith(TABLE_END_PREFIX):
            break
        # Repeated per-page table headers
        if any(line.startswith(p) for p in TABLE_HEADER_PREFIXES):
            continue

        m = TXN_LINE_RE.match(line)
        if m:
            flush()
            raw = {
                "owner": m.group("owner") or "",
                "asset": m.group("asset"),
                "type": m.group("type"),
                "tdate": m.group("tdate"),
                "ndate": m.group("ndate"),
                "amount": m.group("amount"),
                "comment": "",
            }
            mode = "asset"
            continue

        if raw is None:
            continue

        label = None
        label_value = ""
        for name, rex in META_LABEL_RES:
            lm = rex.match(line)
            if lm:
                label = name
                label_value = lm.group(1).strip()
                break

        if label is not None:
            mode = "desc" if label == "description" else "meta"
            if label == "description" and label_value:
                raw["comment"] += (" " if raw["comment"] else "") + label_value
            continue

        if mode == "asset":
            # Wrapped asset name; may also carry the wrapped tail of an
            # amount range ("$15,001 -" / next line "... $50,000").
            part = line
            if raw["amount"].rstrip().endswith("-"):
                am = AMOUNT_TAIL_RE.match(part)
                if am:
                    part = am.group(1)
                    raw["amount"] = raw["amount"].rstrip() + " " + am.group(2)
            if part:
                raw["asset"] += " " + part
        elif mode == "desc":
            raw["comment"] += (" " if raw["comment"] else "") + line
        # mode == "meta": ignore continuation of non-description metadata

    flush()
    return trades, True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("CongressWatch — House PTR Stock Trade Scraper")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    if DRY_RUN:
        print("DRY RUN — nothing will be written")
    print("=" * 60)

    members = load_json(MEMBERS_PATH, [])
    if not members:
        print("[FATAL] data/members.json not found or empty.")
        sys.exit(1)

    house_members = [m for m in members
                     if (m.get("chamber", "") or "").lower() == "house"]
    print(f"Loaded {len(house_members)} House members "
          f"of {len(members)} total")

    district_index, state_index = build_member_indexes(house_members)

    # ── Existing ptr_links per member (for dedup + prev-year check) ──
    existing_links = {}   # bioguide_id -> set of ptr_link URLs
    for m in house_members:
        detail = load_json(os.path.join(DETAILS_DIR, f"{m['id']}.json"), {})
        links = {t.get("ptr_link", "")
                 for t in detail.get("trades", []) if t.get("ptr_link")}
        if links:
            existing_links[m["id"]] = links

    # ── Which index years to fetch ──────────────────────────────────
    current_year = datetime.now(timezone.utc).year
    prev_year = current_year - 1
    prev_marker = f"/ptr-pdfs/{prev_year}/"
    have_prev = any(prev_marker in link
                    for links in existing_links.values() for link in links)

    years = [current_year]
    if not have_prev:
        years.append(prev_year)
        print(f"No {prev_year} House trades on record — "
              f"including {prev_year} index")

    # ── Fetch FD indexes ────────────────────────────────────────────
    all_filings = []
    for i, year in enumerate(years):
        if i > 0:
            time.sleep(REQUEST_DELAY)
        filings = fetch_fd_index(year)
        if filings is None:
            if year == current_year:
                # Early January: current-year index may not exist yet
                print(f"[INDEX] {year} unavailable, falling back to {prev_year}")
                if prev_year not in years:
                    years.append(prev_year)
                continue
            print(f"[INDEX] {year} unavailable, continuing without it")
            continue
        all_filings.extend(filings)

    if not all_filings:
        # Guard: no usable index data — exit WITHOUT writing anything.
        print("[FATAL] No PTR filings could be loaded from any FD index. "
              "Exiting without writing.")
        sys.exit(1)

    print(f"\nTotal PTR filings listed: {len(all_filings)}")

    stats = {
        "filings_listed": len(all_filings),
        "matched": 0,
        "unmatched_skipped": 0,
        "already_have": 0,
        "paper_skipped": 0,
        "capped_skipped": 0,
        "pdfs_parsed": 0,
        "scanned_skipped": 0,
        "parse_empty": 0,
        "trades_found": 0,
        "members_updated": 0,
        "errors": 0,
    }

    # ── Match filings to members ────────────────────────────────────
    to_fetch = []       # (filing, member)
    unmatched_names = []

    for filing in all_filings:
        member = match_filing_to_member(filing, district_index, state_index)
        if member is None:
            stats["unmatched_skipped"] += 1
            unmatched_names.append(
                f"{filing['last']}, {filing['first']} ({filing['state_dst']})")
            continue
        stats["matched"] += 1

        if filing["pdf_url"] in existing_links.get(member["id"], set()):
            stats["already_have"] += 1
            continue

        # Paper filings (DocIDs starting 8/9) are scanned images — skip
        # up front so we don't re-download them every run.
        if not filing["doc_id"].startswith("2"):
            stats["paper_skipped"] += 1
            continue

        to_fetch.append((filing, member))

    if unmatched_names:
        unique = sorted(set(unmatched_names))
        print(f"\nUnmatched filings skipped ({len(unique)} filers): "
              f"{'; '.join(unique[:12])}"
              f"{' ...' if len(unique) > 12 else ''}")

    # ── Cap per-run PDF work: newest first, oldest deferred ─────────
    to_fetch.sort(
        key=lambda fm: parse_date_any(fm[0]["filing_date"]) or datetime.min,
        reverse=True,
    )
    if len(to_fetch) > MAX_NEW_FILINGS:
        skipped = to_fetch[MAX_NEW_FILINGS:]
        stats["capped_skipped"] = len(skipped)
        oldest = skipped[-1][0]
        print(f"\nCapping run at {MAX_NEW_FILINGS} new filings; deferring "
              f"{len(skipped)} older ones (oldest: {oldest['last']} "
              f"{oldest['filing_date']}, DocID {oldest['doc_id']}) "
              f"to future runs")
        to_fetch = to_fetch[:MAX_NEW_FILINGS]

    print(f"\n--- Downloading {len(to_fetch)} new PTR PDFs ---")

    member_trades = {}  # bioguide_id -> [trades]

    for idx, (filing, member) in enumerate(to_fetch):
        if idx > 0:
            time.sleep(REQUEST_DELAY)
        tag = (f"[{idx + 1}/{len(to_fetch)}] {member['name']} "
               f"({filing['state_dst']}) DocID {filing['doc_id']}")
        try:
            resp = http_get(filing["pdf_url"])
            if resp is None or resp.status_code != 200:
                print(f"  {tag}: download failed "
                      f"(HTTP {resp.status_code if resp is not None else '?'})")
                stats["errors"] += 1
                continue

            trades, has_text = parse_ptr_pdf(resp.content, filing["pdf_url"])
            if not has_text:
                print(f"  {tag}: scanned paper filing, skipping")
                stats["scanned_skipped"] += 1
                continue

            stats["pdfs_parsed"] += 1
            if not trades:
                print(f"  {tag}: no transactions parsed")
                stats["parse_empty"] += 1
                continue

            stats["trades_found"] += len(trades)
            print(f"  {tag}: {len(trades)} trades "
                  f"(filed {filing['filing_date']})")
            member_trades.setdefault(member["id"], []).extend(trades)

        except Exception as e:
            print(f"  {tag}: error: {e}")
            stats["errors"] += 1

    # ── Save results ────────────────────────────────────────────────
    print("\n--- Saving results ---")

    members_by_id = {m["id"]: m for m in members}

    for bid, new_trades in member_trades.items():
        detail_path = os.path.join(DETAILS_DIR, f"{bid}.json")
        detail = load_json(detail_path, {})

        existing = detail.get("trades", [])
        seen = {trade_key(t) for t in existing}

        added = []
        for t in new_trades:
            k = trade_key(t)
            if k not in seen:
                added.append(t)
                seen.add(k)

        if not added:
            continue

        all_trades = existing + added
        detail["trades"] = all_trades
        detail["trades_updated"] = datetime.now(timezone.utc).isoformat()
        detail["trade_count"] = len(all_trades)
        detail["latest_trade_date"] = latest_iso_date(all_trades)

        if not DRY_RUN:
            save_json(detail_path, detail)

        if bid in members_by_id:
            members_by_id[bid]["trade_count"] = len(all_trades)
            members_by_id[bid]["latest_trade_date"] = detail["latest_trade_date"]

        stats["members_updated"] += 1
        name = members_by_id.get(bid, {}).get("name", bid)
        print(f"  {name}: +{len(added)} trades ({len(all_trades)} total)"
              f"{' [dry-run]' if DRY_RUN else ''}")

    # Only touch members.json when something actually changed.
    if stats["members_updated"] > 0 and members and not DRY_RUN:
        save_json(MEMBERS_PATH, members)
        print("  members.json updated")
    else:
        print("  members.json unchanged")

    # ── Summary ─────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("HOUSE PTR SCRAPER — COMPLETE")
    print("=" * 60)
    print(f"PTR filings listed:    {stats['filings_listed']}")
    print(f"Matched to members:    {stats['matched']}")
    print(f"Unmatched (skipped):   {stats['unmatched_skipped']}")
    print(f"Already ingested:      {stats['already_have']}")
    print(f"Paper (scanned) skips: {stats['paper_skipped'] + stats['scanned_skipped']}")
    print(f"Deferred by cap:       {stats['capped_skipped']}")
    print(f"PDFs parsed:           {stats['pdfs_parsed']}")
    print(f"Empty parses:          {stats['parse_empty']}")
    print(f"Trades found:          {stats['trades_found']}")
    print(f"Members updated:       {stats['members_updated']}")
    print(f"Errors:                {stats['errors']}")
    print(f"Finished: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
