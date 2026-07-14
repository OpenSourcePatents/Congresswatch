#!/usr/bin/env python3
"""
fetch_senate_efd.py — Senate eFD Stock Trade Scraper
=====================================================
Pulls Periodic Transaction Reports (PTRs) directly from
efdsearch.senate.gov for all current senators.

Data source:
  - Senate Electronic Financial Disclosure (eFD) system
  - https://efdsearch.senate.gov/

Output:
  - data/details/{bioguide_id}.json — trades[] array (safe merge)
  - data/members.json — trade_count + latest_trade_date

Run:
  python fetch_senate_efd.py
"""

import json
import os
import re
import sys
import time
import requests
from datetime import datetime, timezone
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
MEMBERS_PATH = os.path.join(BASE_DIR, "data", "members.json")
DETAILS_DIR  = os.path.join(BASE_DIR, "data", "details")

EFD_BASE   = "https://efdsearch.senate.gov"
EFD_HOME   = f"{EFD_BASE}/search/home/"
EFD_SEARCH = f"{EFD_BASE}/search/report/data/"

USER_AGENT    = ("CongressWatch/1.0 "
                 "(public-interest-research; "
                 "mailto:project.congress.watch@gmail.com)")
REQUEST_DELAY = 1.5   # seconds between requests
MAX_FILINGS   = 500   # cap filings per run (safety)
MAX_RETRIES   = 3
BACKOFF_BASE  = 3     # seconds, doubles each retry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path, default):
    """Guarded load: a corrupt existing file aborts the run instead of
    silently becoming `default` and then getting overwritten."""
    if os.path.exists(path):
        if os.path.getsize(path) == 0:
            return default
        with open(path, "r") as f:
            try:
                return json.load(f)
            except Exception as e:
                raise SystemExit(f"ABORT: {path} exists but failed to parse "
                                 f"({e}). Refusing to continue.")
    return default


def save_json(path, data):
    """Atomic write: temp file + os.replace so a crash never truncates."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def to_iso_date(s):
    """Normalize MM/DD/YYYY or ISO-ish strings to YYYY-MM-DD ('' if bad)."""
    if not s:
        return ""
    s = str(s).strip()[:10]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def normalize_name(name):
    """Lowercase, strip suffixes like Jr/Sr/III, remove non-alpha."""
    name = name.lower().strip()
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", name)
    name = re.sub(r"[^a-z\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def parse_efd_name(raw):
    """
    Parse eFD name formats into (last, [first_parts]).

    Handles:
      'McConnell, A. Mitchell'  -> ('mcconnell', ['mitchell'])
      'Warren, Elizabeth'       -> ('warren', ['elizabeth'])
      'Elizabeth Warren'        -> ('warren', ['elizabeth'])
    """
    raw = raw.strip()
    # Strip honorific prefixes
    for prefix in ["Sen. ", "Senator ", "Hon. ", "Honorable "]:
        if raw.startswith(prefix):
            raw = raw[len(prefix):]

    if "," in raw:
        parts = raw.split(",", 1)
        last = parts[0].strip()
        # Keep name words longer than 1 char (skip initials like "A.")
        first_parts = [
            normalize_name(p)
            for p in parts[1].strip().split()
            if len(re.sub(r"[^a-z]", "", p.lower())) > 1
        ]
    else:
        parts = raw.split()
        last = parts[-1] if parts else ""
        first_parts = [
            normalize_name(p)
            for p in parts[:-1]
            if len(re.sub(r"[^a-z]", "", p.lower())) > 1
        ]

    return normalize_name(last), first_parts


def first_name_match(efd_first_parts, member_name):
    """Check if any eFD name part fuzzy-matches the member's first name."""
    m_first = normalize_name(member_name.split()[0]) if member_name.split() else ""
    if not m_first:
        return False
    for part in efd_first_parts:
        if part == m_first:
            return True
        # 'mitch' matches 'mitchell' — check 3-char prefix overlap
        if len(part) >= 3 and len(m_first) >= 3:
            if part.startswith(m_first[:3]) or m_first.startswith(part[:3]):
                return True
    return False


def trade_key(t):
    """Dedup key for a trade record."""
    return (
        t.get("ptr_link", ""),
        t.get("transaction_date", ""),
        t.get("ticker", ""),
        t.get("type", ""),
        t.get("amount", ""),
    )


# ---------------------------------------------------------------------------
# Senate eFD client
# ---------------------------------------------------------------------------

class SenateEFDClient:
    """Handles consent gate, search, and detail page fetching."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.agreed = False

    def _request(self, method, url, **kwargs):
        """Request with retry + exponential backoff on 429/5xx."""
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.session.request(method, url, timeout=30, **kwargs)
                if resp.status_code == 429:
                    wait = BACKOFF_BASE * (2 ** attempt)
                    print(f"    Rate limited, waiting {wait}s...")
                    time.sleep(wait)
                    continue
                if resp.status_code >= 500:
                    wait = BACKOFF_BASE * (2 ** attempt)
                    print(f"    Server error {resp.status_code}, retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                return resp
            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    wait = BACKOFF_BASE * (2 ** attempt)
                    print(f"    Request error: {e}, retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    raise
        return None

    def accept_agreement(self):
        """Navigate the consent gate at efdsearch.senate.gov."""
        print("[EFD] Accepting agreement...")

        # GET home page to obtain CSRF token
        resp = self._request("GET", EFD_HOME)
        if not resp or resp.status_code != 200:
            print(f"[EFD] Failed to load home page: "
                  f"{resp.status_code if resp else 'no response'}")
            return False

        # Django CSRF: token in cookie or hidden input
        csrf_token = self.session.cookies.get("csrftoken", "")
        if not csrf_token:
            soup = BeautifulSoup(resp.text, "html.parser")
            inp = soup.find("input", {"name": "csrfmiddlewaretoken"})
            if inp:
                csrf_token = inp.get("value", "")

        if not csrf_token:
            print("[EFD] WARNING: No CSRF token found, attempting without...")

        time.sleep(REQUEST_DELAY)

        # POST agreement acceptance
        resp = self._request(
            "POST", EFD_HOME,
            data={
                "csrfmiddlewaretoken": csrf_token,
                "prohibition_agreement": "1",
            },
            headers={
                "Referer": EFD_HOME,
                "Origin": EFD_BASE,
            },
            allow_redirects=True,
        )

        if not resp:
            print("[EFD] Agreement POST returned no response")
            return False

        # Verify we can actually access the search page — this is the only
        # trustworthy signal. (A previous fallback accepted any 200/302 POST
        # response, so a re-rendered consent page counted as success and the
        # run silently "found" 0 filings instead of failing visibly.)
        time.sleep(REQUEST_DELAY)
        test = self._request("GET", f"{EFD_BASE}/search/")
        if test and test.status_code == 200 and "home" not in test.url:
            self.agreed = True
            print("[EFD] Agreement accepted (search page reachable)")
            return True

        print(f"[EFD] Agreement FAILED verification: "
              f"POST status={resp.status_code}, "
              f"search-page={'%s -> %s' % (test.status_code, test.url) if test else 'no response'}")
        return False

    def search_filings(self, start=0, length=100):
        """
        Search for senator PTR filings via DataTables AJAX endpoint.
        Returns parsed JSON response with .data[], .recordsTotal, etc.
        """
        csrf_token = self.session.cookies.get("csrftoken", "")

        # DataTables server-side processing payload
        payload = {
            "draw": "1",
            "start": str(start),
            "length": str(length),
            "search[value]": "",
            "search[regex]": "false",
            "order[0][column]": "4",   # sort by date filed
            "order[0][dir]": "desc",   # newest first
            "first_name": "",
            "last_name": "",
            "filer_type": "1",         # 1 = Senator
            "report_type": "11",       # 11 = Periodic Transaction Report
            "csrfmiddlewaretoken": csrf_token,
        }

        # DataTables column definitions (5 columns)
        for i in range(5):
            payload[f"columns[{i}][data]"] = str(i)
            payload[f"columns[{i}][name]"] = ""
            payload[f"columns[{i}][searchable]"] = "true"
            payload[f"columns[{i}][orderable]"] = "true"
            payload[f"columns[{i}][search][value]"] = ""
            payload[f"columns[{i}][search][regex]"] = "false"

        resp = self._request(
            "POST", EFD_SEARCH,
            data=payload,
            headers={
                "Referer": f"{EFD_BASE}/search/",
                "X-Requested-With": "XMLHttpRequest",
            },
        )

        if not resp or resp.status_code != 200:
            print(f"    Search failed: "
                  f"{resp.status_code if resp else 'no response'}")
            return {"data": [], "recordsTotal": 0}

        try:
            return resp.json()
        except (ValueError, requests.exceptions.JSONDecodeError):
            print("[EFD] Search returned non-JSON "
                  "(agreement may not have been accepted)")
            return {"data": [], "recordsTotal": 0}

    def fetch_ptr_page(self, url):
        """Fetch a PTR detail page. Returns HTML string or None."""
        resp = self._request("GET", url)
        if not resp or resp.status_code != 200:
            return None
        return resp.text


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_search_results(response_json):
    """
    Parse DataTables search response into a list of filing dicts.
    Each row is [name_html, office, filer_type, report_type, date_filed].
    """
    filings = []
    for row in response_json.get("data", []):
        if not row or len(row) < 5:
            continue

        # Column 0: senator name with <a href="...">
        soup = BeautifulSoup(str(row[0]), "html.parser")
        link = soup.find("a")
        if not link:
            continue

        href = link.get("href", "")
        name = link.get_text(strip=True)

        # Skip paper filings (scanned PDFs — can't parse HTML tables)
        if "/paper/" in href:
            continue

        # Only electronic PTR filings
        if "/ptr/" not in href:
            continue

        # Build absolute URL
        url = href if href.startswith("http") else f"{EFD_BASE}{href}"

        # Column 4: date filed
        date_filed = ""
        if len(row) > 4:
            date_soup = BeautifulSoup(str(row[4]), "html.parser")
            date_filed = date_soup.get_text(strip=True)

        filings.append({
            "name": name,
            "url": url,
            "date_filed": date_filed,
        })

    return filings


# Map table header text -> trade field name
HEADER_MAP = {
    "transaction date": "transaction_date",
    "owner":            "owner",
    "ticker":           "ticker",
    "asset name":       "asset_description",
    "asset type":       "asset_type",
    "type":             "type",
    "transaction type": "type",
    "amount":           "amount",
    "comment":          "comment",
    "comments":         "comment",
}


def parse_ptr_page(html, ptr_url=""):
    """
    Parse a PTR detail page HTML into a list of trade dicts.
    Returns [] for paper filings or pages with no transaction table.
    """
    soup = BeautifulSoup(html, "html.parser")
    trades = []

    # Find the transactions table by looking for trade-related headers
    table = None
    for t in soup.find_all("table"):
        header_text = " ".join(
            th.get_text(strip=True).lower() for th in t.find_all("th")
        )
        if ("transaction" in header_text or "ticker" in header_text
                or "asset" in header_text):
            table = t
            break

    if not table:
        return trades

    # Map column positions to field names using header text
    col_map = []   # index -> field name or None
    for th in table.find_all("th"):
        h = th.get_text(strip=True).lower()
        field = None
        for pattern, name in HEADER_MAP.items():
            if pattern in h:
                field = name
                break
        col_map.append(field)

    # Parse each row
    tbody = table.find("tbody") or table
    for row in tbody.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        trade = {
            "transaction_date": "",
            "owner": "--",
            "ticker": "--",
            "asset_description": "",
            "asset_type": "",
            "type": "",
            "amount": "",
            "comment": "--",
            "ptr_link": ptr_url,
        }

        for i, cell in enumerate(cells):
            if i >= len(col_map) or col_map[i] is None:
                continue
            field = col_map[i]
            # Store plain text only — HTML fragments in data are an XSS
            # hazard and render inconsistently (legacy rows kept anchors)
            trade[field] = cell.get_text(strip=True) or ("--" if field != "asset_description" else "")

        # Normalize dates to ISO so date comparisons work everywhere
        iso = to_iso_date(trade["transaction_date"])
        if iso:
            trade["transaction_date"] = iso

        # Only keep rows with at least a date or an asset
        if trade["transaction_date"] or trade["asset_description"]:
            trades.append(trade)

    return trades


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("CongressWatch — Senate eFD Stock Trade Scraper")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    # Load members, filter to senators
    members = load_json(MEMBERS_PATH, [])
    if not members:
        print("[FATAL] data/members.json not found or empty.")
        sys.exit(1)

    senators = [m for m in members
                if (m.get("chamber", "") or "").lower() == "senate"]
    print(f"Loaded {len(senators)} senators from {len(members)} total members")

    # Build name lookup: normalized last name -> [member dicts]
    by_last = {}
    for m in senators:
        parts = m.get("name", "").split()
        if not parts:
            continue
        last = normalize_name(parts[-1])
        by_last.setdefault(last, []).append(m)

    # ── Connect to eFD ──────────────────────────────────────────
    client = SenateEFDClient()
    if not client.accept_agreement():
        print("[FATAL] Could not accept eFD agreement. Exiting.")
        sys.exit(1)

    # ── Search for PTR filings ──────────────────────────────────
    print("\n--- Searching for senator PTR filings ---")
    all_filings = []
    start = 0
    page_size = 100

    while len(all_filings) < MAX_FILINGS:
        time.sleep(REQUEST_DELAY)
        data = client.search_filings(start=start, length=page_size)
        raw_rows = len(data.get("data", []) or [])
        filings = parse_search_results(data)
        total = data.get("recordsTotal", 0)

        print(f"  Page {start // page_size + 1}: "
              f"{len(filings)} electronic PTRs of {raw_rows} rows "
              f"(server total: {total})")

        # Stop only when the server returns no rows at all. A page can be
        # all paper filings (filtered out) while later pages still hold
        # electronic PTRs — that must not end pagination early.
        if raw_rows == 0:
            break

        all_filings.extend(filings)
        start += page_size

        if start >= total:
            break

    all_filings = all_filings[:MAX_FILINGS]
    print(f"\nTotal electronic PTR filings found: {len(all_filings)}")

    if not all_filings:
        print("[WARN] No filings found. The eFD site may be down "
              "or the search format may have changed.")
        sys.exit(0)

    # ── Match filings to senators ───────────────────────────────
    matched = {}    # bioguide_id -> [filing dicts]
    unmatched = []

    for filing in all_filings:
        last, first_parts = parse_efd_name(filing["name"])
        candidates = by_last.get(last, [])

        match = None
        if len(candidates) == 1:
            match = candidates[0]
        elif len(candidates) > 1:
            # Disambiguate by first name; never blind-attribute — a former
            # senator's filing must not land on a same-surname sitting one
            for c in candidates:
                if first_name_match(first_parts, c["name"]):
                    match = c
                    break

        if match:
            bid = match["id"]
            matched.setdefault(bid, []).append(filing)
        else:
            unmatched.append(filing["name"])

    if unmatched:
        unique = sorted(set(unmatched))
        print(f"\n  Unmatched names ({len(unique)}): "
              f"{', '.join(unique[:15])}")

    print(f"  Matched {len(all_filings) - len(unmatched)}/{len(all_filings)} "
          f"filings to {len(matched)} senators")

    # ── Fetch + parse each PTR detail page ──────────────────────
    print("\n--- Fetching PTR detail pages ---")

    stats = {
        "senators_updated": 0,
        "filings_parsed": 0,
        "trades_found": 0,
        "skipped_existing": 0,
        "errors": 0,
    }

    senator_trades = {}  # bioguide_id -> [trade dicts]

    for bid, filings in matched.items():
        name = next((m["name"] for m in senators if m["id"] == bid), bid)

        # Check which PTR URLs we already have trades for
        detail_path = os.path.join(DETAILS_DIR, f"{bid}.json")
        existing_detail = load_json(detail_path, {})
        existing_ptr_urls = {
            t.get("ptr_link", "")
            for t in existing_detail.get("trades", [])
            if t.get("ptr_link")
        }

        new_filings = [
            f for f in filings if f["url"] not in existing_ptr_urls
        ]

        if not new_filings:
            stats["skipped_existing"] += len(filings)
            continue

        print(f"\n  {name} ({bid}): {len(new_filings)} new / "
              f"{len(filings)} total filings")

        trades_for_senator = []

        for filing in new_filings:
            time.sleep(REQUEST_DELAY)

            try:
                html = client.fetch_ptr_page(filing["url"])
                if not html:
                    print(f"    {filing['date_filed']}: "
                          f"empty response (skipping)")
                    stats["errors"] += 1
                    continue

                trades = parse_ptr_page(html, filing["url"])
                trades_for_senator.extend(trades)
                stats["filings_parsed"] += 1

                if trades:
                    print(f"    {filing['date_filed']}: "
                          f"{len(trades)} trades")
                    stats["trades_found"] += len(trades)
                else:
                    print(f"    {filing['date_filed']}: "
                          f"no trades parsed (may be empty or paper)")
            except Exception as e:
                print(f"    Error parsing {filing['url']}: {e}")
                stats["errors"] += 1

        if trades_for_senator:
            senator_trades[bid] = trades_for_senator

    # ── Save results ────────────────────────────────────────────
    print("\n--- Saving results ---")

    members_by_id = {m["id"]: m for m in members}

    for bid, new_trades in senator_trades.items():
        detail_path = os.path.join(DETAILS_DIR, f"{bid}.json")
        detail = load_json(detail_path, {})

        # Deduplicate: merge new trades with existing
        existing = detail.get("trades", [])
        seen_keys = {trade_key(t) for t in existing}

        added = []
        for t in new_trades:
            k = trade_key(t)
            if k not in seen_keys:
                added.append(t)
                seen_keys.add(k)

        all_trades = existing + added

        detail["trades"] = all_trades
        detail["trades_updated"] = datetime.now(timezone.utc).isoformat()
        detail["trade_count"] = len(all_trades)

        # Find latest trade date — parse first: legacy rows store
        # MM/DD/YYYY, where string max() sorts by month, not year
        dates = [to_iso_date(t.get("transaction_date", ""))
                 for t in all_trades]
        dates = [d for d in dates if d]
        detail["latest_trade_date"] = max(dates) if dates else ""

        save_json(detail_path, detail)

        # Update members.json entry
        if bid in members_by_id:
            members_by_id[bid]["trade_count"] = len(all_trades)
            members_by_id[bid]["latest_trade_date"] = detail["latest_trade_date"]

        stats["senators_updated"] += 1
        name = members_by_id.get(bid, {}).get("name", bid)
        print(f"  {name}: +{len(added)} new trades "
              f"({len(all_trades)} total)")

    # Save members.json
    save_json(MEMBERS_PATH, members)

    # ── Summary ─────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("SENATE eFD SCRAPER — COMPLETE")
    print("=" * 60)
    print(f"Senators updated:      {stats['senators_updated']}")
    print(f"Filings parsed:        {stats['filings_parsed']}")
    print(f"Filings skipped:       {stats['skipped_existing']} (already have)")
    print(f"Trades found:          {stats['trades_found']}")
    print(f"Errors:                {stats['errors']}")
    print(f"Finished: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
