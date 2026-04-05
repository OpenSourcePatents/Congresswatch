#!/usr/bin/env python3
"""
fetch_house_trades.py — House Financial Disclosure PTR Scraper
===============================================================
Scrapes House member Periodic Transaction Reports from the
House Clerk financial disclosure system.

Source: https://disclosures-clerk.house.gov/FinancialDisclosure

Output:
  - data/details/{bioguide_id}.json — trades[] array (safe merge)
  - data/members.json — trade_count + latest_trade_date

Reuses Supabase trades table (same schema as fetch_senate_efd.py):

-- CREATE TABLE IF NOT EXISTS public.trades (
--   id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
--   bioguide_id TEXT NOT NULL,
--   transaction_date DATE,
--   ticker TEXT,
--   asset_description TEXT,
--   asset_type TEXT,
--   trade_type TEXT,
--   amount_range TEXT,
--   owner TEXT,
--   ptr_link TEXT,
--   source TEXT DEFAULT 'senate_efd',
--   created_at TIMESTAMPTZ DEFAULT now(),
--   UNIQUE(bioguide_id, transaction_date, ticker, trade_type, amount_range)
-- );

Env vars (optional):
  SUPABASE_URL
  SUPABASE_SERVICE_KEY

Run:
  pip install requests beautifulsoup4
  python fetch_house_trades.py
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

DISCLOSURES_BASE   = "https://disclosures-clerk.house.gov"
DISCLOSURES_SEARCH = f"{DISCLOSURES_BASE}/FinancialDisclosure"
DISCLOSURES_PTR    = f"{DISCLOSURES_BASE}/public_disc/ptr-pdfs"

USER_AGENT    = "CongressWatch/1.0 (public-interest-research)"
REQUEST_DELAY = 1.5
MAX_RETRIES   = 3
BACKOFF_BASE  = 3

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return default
    return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def normalize_name(name):
    """Lowercase, strip suffixes and non-alpha."""
    name = name.lower().strip()
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v|hon|rep)\b\.?", "", name)
    name = re.sub(r"[^a-z\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


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
# ASPX session client
# ---------------------------------------------------------------------------

class HouseDisclosureClient:
    """Handles ASP.NET WebForms session for House financial disclosure search."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.viewstate = ""
        self.viewstate_gen = ""
        self.event_validation = ""

    def _request(self, method, url, **kwargs):
        """Request with retry + backoff."""
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.session.request(method, url, timeout=30, **kwargs)
                if resp.status_code == 429 or resp.status_code >= 500:
                    wait = BACKOFF_BASE * (2 ** attempt)
                    print(f"    HTTP {resp.status_code}, retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                return resp
            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    wait = BACKOFF_BASE * (2 ** attempt)
                    print(f"    Error: {e}, retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    raise
        return None

    def _extract_aspnet_fields(self, html):
        """Extract ASP.NET hidden form fields."""
        soup = BeautifulSoup(html, "html.parser")
        for field, attr in [("viewstate", "__VIEWSTATE"),
                            ("viewstate_gen", "__VIEWSTATEGENERATOR"),
                            ("event_validation", "__EVENTVALIDATION")]:
            inp = soup.find("input", {"name": attr})
            if inp:
                setattr(self, field, inp.get("value", ""))

    def init_session(self):
        """GET the search page to initialize ASPX session."""
        print("[HOUSE] Loading disclosure search page...")
        resp = self._request("GET", DISCLOSURES_SEARCH)
        if not resp or resp.status_code != 200:
            print(f"[HOUSE] Failed: {resp.status_code if resp else 'no response'}")
            return False
        self._extract_aspnet_fields(resp.text)
        print("[HOUSE] Session initialized")
        return True

    def search_member(self, last_name, year=""):
        """
        Search for financial disclosures by last name.
        Returns list of filing dicts: {name, url, report_type, filing_date}.
        """
        time.sleep(REQUEST_DELAY)

        payload = {
            "__VIEWSTATE": self.viewstate,
            "__VIEWSTATEGENERATOR": self.viewstate_gen,
            "__EVENTVALIDATION": self.event_validation,
            "LastName": last_name,
            "FilingYear": year,
            "State": "",
            "District": "",
        }

        resp = self._request(
            "POST", DISCLOSURES_SEARCH,
            data=payload,
            headers={"Referer": DISCLOSURES_SEARCH},
        )

        if not resp or resp.status_code != 200:
            return []

        # Update ASPX fields for next request
        self._extract_aspnet_fields(resp.text)

        return self._parse_search_results(resp.text)

    def _parse_search_results(self, html):
        """Parse the search results page for filing links."""
        soup = BeautifulSoup(html, "html.parser")
        filings = []

        # Look for result tables or lists
        # House disclosure results are typically in a table or div with links
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True).lower()

            # Look for PTR-related links
            is_ptr = ("ptr" in text or "periodic" in text
                      or "transaction" in text or "/ptr" in href.lower())

            if not is_ptr:
                continue

            # Skip PDF links (paper filings)
            if href.lower().endswith(".pdf"):
                continue

            url = href if href.startswith("http") else f"{DISCLOSURES_BASE}{href}"

            # Try to extract filing date from surrounding text
            parent = a.find_parent("tr") or a.find_parent("div")
            date_text = ""
            if parent:
                for td in parent.find_all("td"):
                    cell = td.get_text(strip=True)
                    if re.match(r"\d{1,2}/\d{1,2}/\d{4}", cell):
                        date_text = cell
                        break

            filings.append({
                "name": a.get_text(strip=True),
                "url": url,
                "filing_date": date_text,
            })

        return filings

    def fetch_filing_page(self, url):
        """Fetch an electronic PTR filing page."""
        resp = self._request("GET", url)
        if not resp or resp.status_code != 200:
            return None
        return resp.text


# ---------------------------------------------------------------------------
# PTR page parser
# ---------------------------------------------------------------------------

HEADER_MAP = {
    "transaction date": "transaction_date",
    "owner":            "owner",
    "ticker":           "ticker",
    "asset":            "asset_description",
    "description":      "asset_description",
    "type":             "type",
    "transaction type": "type",
    "amount":           "amount",
    "cap. gains":       "cap_gains",
    "notification date": "notification_date",
}


def parse_ptr_page(html, ptr_url=""):
    """Parse a House PTR electronic filing page for trades."""
    soup = BeautifulSoup(html, "html.parser")
    trades = []

    # Find the transactions table
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

    # Map columns
    col_map = []
    for th in table.find_all("th"):
        h = th.get_text(strip=True).lower()
        field = None
        for pattern, name in HEADER_MAP.items():
            if pattern in h:
                field = name
                break
        col_map.append(field)

    # Parse rows
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
            if field == "asset_description":
                trade[field] = cell.decode_contents().strip()
            elif field in trade:
                trade[field] = cell.get_text(strip=True) or "--"

        if trade["transaction_date"] or trade["asset_description"]:
            trades.append(trade)

    return trades


# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------

def supabase_upsert_trades(bid, trades):
    """Upsert trades to Supabase trades table."""
    if not SUPABASE_URL or not SUPABASE_KEY or not trades:
        return 0

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }

    rows = []
    for t in trades:
        rows.append({
            "bioguide_id": bid,
            "transaction_date": t.get("transaction_date") or None,
            "ticker": t.get("ticker", ""),
            "asset_description": t.get("asset_description", ""),
            "asset_type": t.get("asset_type", ""),
            "trade_type": t.get("type", ""),
            "amount_range": t.get("amount", ""),
            "owner": t.get("owner", ""),
            "ptr_link": t.get("ptr_link", ""),
            "source": "house_clerk",
        })

    success = 0
    for i in range(0, len(rows), 100):
        chunk = rows[i:i + 100]
        try:
            r = requests.post(
                f"{SUPABASE_URL}/rest/v1/trades",
                headers=headers,
                json=chunk,
                timeout=30,
            )
            if r.status_code in (200, 201):
                success += len(chunk)
            else:
                print(f"    Supabase batch {i}: {r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"    Supabase error: {e}")

    return success


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("CongressWatch — House PTR Stock Trade Scraper")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    members = load_json(MEMBERS_PATH, [])
    if not members:
        print("[FATAL] data/members.json not found or empty.")
        sys.exit(1)

    house_members = [m for m in members
                     if (m.get("chamber", "") or "").lower() == "house"]
    print(f"Loaded {len(house_members)} House members")

    # Initialize ASPX session
    client = HouseDisclosureClient()
    if not client.init_session():
        print("[FATAL] Could not initialize House disclosure session.")
        sys.exit(1)

    # Group members by last name to reduce searches
    by_last_name = {}
    for m in house_members:
        parts = m.get("name", "").split()
        if not parts:
            continue
        last = parts[-1]
        by_last_name.setdefault(last, []).append(m)

    stats = {
        "members_searched": 0,
        "filings_found": 0,
        "filings_parsed": 0,
        "trades_found": 0,
        "members_updated": 0,
        "errors": 0,
        "supabase_upserted": 0,
    }

    member_trades = {}  # bioguide_id -> [trades]

    # Search by unique last names
    unique_lasts = sorted(by_last_name.keys())
    print(f"\n--- Searching {len(unique_lasts)} unique last names ---")

    for idx, last_name in enumerate(unique_lasts):
        members_with_name = by_last_name[last_name]
        print(f"  [{idx+1}/{len(unique_lasts)}] {last_name} "
              f"({len(members_with_name)} members)")

        try:
            filings = client.search_member(last_name)
            stats["members_searched"] += 1

            if not filings:
                continue

            stats["filings_found"] += len(filings)
            print(f"    Found {len(filings)} electronic PTR filings")

            # Check which filings are new
            existing_urls = set()
            for m in members_with_name:
                detail = load_json(
                    os.path.join(DETAILS_DIR, f"{m['id']}.json"), {})
                for t in detail.get("trades", []):
                    if t.get("ptr_link"):
                        existing_urls.add(t["ptr_link"])

            for filing in filings:
                if filing["url"] in existing_urls:
                    continue

                time.sleep(REQUEST_DELAY)

                try:
                    html = client.fetch_filing_page(filing["url"])
                    if not html:
                        stats["errors"] += 1
                        continue

                    trades = parse_ptr_page(html, filing["url"])
                    stats["filings_parsed"] += 1

                    if trades:
                        stats["trades_found"] += len(trades)
                        print(f"    {filing.get('filing_date', '?')}: "
                              f"{len(trades)} trades")

                        # Match to member by last name + first name
                        # (from filing page or our member list)
                        bid = members_with_name[0]["id"]
                        if len(members_with_name) > 1:
                            filing_text = normalize_name(filing.get("name", ""))
                            for m in members_with_name:
                                m_first = normalize_name(m["name"].split()[0])
                                if m_first in filing_text:
                                    bid = m["id"]
                                    break

                        member_trades.setdefault(bid, []).extend(trades)

                except Exception as e:
                    print(f"    Error: {e}")
                    stats["errors"] += 1

        except Exception as e:
            print(f"    Search error: {e}")
            stats["errors"] += 1

    # Save results
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

        all_trades = existing + added
        detail["trades"] = all_trades
        detail["trades_updated"] = datetime.now(timezone.utc).isoformat()
        detail["trade_count"] = len(all_trades)

        dates = [t.get("transaction_date", "")
                 for t in all_trades if t.get("transaction_date")]
        detail["latest_trade_date"] = max(dates) if dates else ""

        save_json(detail_path, detail)

        if bid in members_by_id:
            members_by_id[bid]["trade_count"] = len(all_trades)
            members_by_id[bid]["latest_trade_date"] = detail["latest_trade_date"]

        count = supabase_upsert_trades(bid, added)
        stats["supabase_upserted"] += count
        stats["members_updated"] += 1

        name = members_by_id.get(bid, {}).get("name", bid)
        print(f"  {name}: +{len(added)} trades ({len(all_trades)} total)")

    save_json(MEMBERS_PATH, members)

    # Summary
    print("\n" + "=" * 60)
    print("HOUSE PTR SCRAPER — COMPLETE")
    print("=" * 60)
    print(f"Last names searched:   {stats['members_searched']}")
    print(f"Filings found:         {stats['filings_found']}")
    print(f"Filings parsed:        {stats['filings_parsed']}")
    print(f"Trades found:          {stats['trades_found']}")
    print(f"Members updated:       {stats['members_updated']}")
    print(f"Errors:                {stats['errors']}")
    if SUPABASE_URL:
        print(f"Supabase upserted:     {stats['supabase_upserted']}")
    print(f"Finished: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
