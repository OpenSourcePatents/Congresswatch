#!/usr/bin/env python3
"""
fetch_travel_pdf.py — House Foreign Travel PDF Scraper
=======================================================
Downloads and parses House Clerk foreign travel disclosure PDFs
using pdfplumber for table extraction.

Source: https://clerk.house.gov/public_disc/travel/

Output:
  - data/details/{bioguide_id}.json — travel[] array (safe merge)
  - data/members.json — travel_count

Supabase table schema — run in SQL Editor before first use:

-- CREATE TABLE IF NOT EXISTS public.travel (
--   id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
--   bioguide_id TEXT NOT NULL,
--   destination_country TEXT,
--   departure_date DATE,
--   return_date DATE,
--   sponsor TEXT,
--   total_cost NUMERIC(12,2),
--   currency TEXT DEFAULT 'USD',
--   report_type TEXT,
--   source TEXT DEFAULT 'house_clerk',
--   created_at TIMESTAMPTZ DEFAULT now(),
--   UNIQUE(bioguide_id, departure_date, destination_country)
-- );
-- CREATE INDEX IF NOT EXISTS idx_travel_member ON public.travel(bioguide_id);
-- ALTER TABLE public.travel ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY "travel_read" ON public.travel FOR SELECT TO anon USING (true);
-- CREATE POLICY "travel_service_all" ON public.travel FOR ALL TO service_role USING (true) WITH CHECK (true);

Env vars (optional):
  SUPABASE_URL
  SUPABASE_SERVICE_KEY

Run:
  pip install pdfplumber requests beautifulsoup4
  python fetch_travel_pdf.py
"""

import json
import os
import re
import sys
import time
import tempfile
import requests
from datetime import datetime, timezone
from bs4 import BeautifulSoup

try:
    import pdfplumber
except ImportError:
    print("[FATAL] pdfplumber not installed. Run: pip install pdfplumber")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
MEMBERS_PATH = os.path.join(BASE_DIR, "data", "members.json")
DETAILS_DIR  = os.path.join(BASE_DIR, "data", "details")

TRAVEL_INDEX = "https://clerk.house.gov/public_disc/travel/"
USER_AGENT   = ("CongressWatch/1.0 "
                "(public-interest-research; "
                "mailto:project.congress.watch@gmail.com)")
REQUEST_DELAY = 2.0  # seconds between PDF downloads

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# How many years of reports to download
LOOKBACK_YEARS = 3


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
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v|hon|rep|mr|ms|mrs|dr)\b\.?", "", name)
    name = re.sub(r"[^a-z\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def parse_date(s):
    """Try parsing common date formats to YYYY-MM-DD."""
    if not s or not s.strip():
        return ""
    s = s.strip()
    for fmt in ["%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%B %d, %Y",
                "%b %d, %Y", "%m-%d-%Y"]:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def parse_cost(s):
    """Extract numeric cost from strings like '$2,345.67'."""
    if not s:
        return 0.0
    cleaned = re.sub(r"[^\d.]", "", s.replace(",", ""))
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# PDF discovery
# ---------------------------------------------------------------------------

def discover_pdf_links(session):
    """Fetch the travel index page and extract PDF links."""
    print(f"[TRAVEL] Fetching index: {TRAVEL_INDEX}")
    resp = session.get(TRAVEL_INDEX, timeout=30)
    if resp.status_code != 200:
        print(f"[TRAVEL] Index returned {resp.status_code}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    links = []
    current_year = datetime.now().year

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.lower().endswith(".pdf"):
            continue

        url = href if href.startswith("http") else TRAVEL_INDEX.rstrip("/") + "/" + href.lstrip("/")

        # Filter to recent years
        year_match = re.search(r"(20\d{2})", href)
        if year_match:
            year = int(year_match.group(1))
            if year < current_year - LOOKBACK_YEARS:
                continue

        links.append(url)

    print(f"[TRAVEL] Found {len(links)} PDF links within {LOOKBACK_YEARS}-year window")
    return links


# ---------------------------------------------------------------------------
# PDF parsing
# ---------------------------------------------------------------------------

# Patterns for identifying header rows
HEADER_KEYWORDS = {"name", "traveler", "member", "destination", "departure",
                   "return", "sponsor", "cost", "total", "per diem",
                   "transportation"}


def is_header_row(row):
    """Check if a row looks like a table header."""
    text = " ".join((c or "").lower() for c in row)
    hits = sum(1 for kw in HEADER_KEYWORDS if kw in text)
    return hits >= 3


def map_columns(header_row):
    """Map column indices to field names based on header text."""
    col_map = {}
    for i, cell in enumerate(header_row):
        h = (cell or "").lower().strip()
        if any(w in h for w in ["name", "traveler", "member"]):
            col_map["name"] = i
        elif "destination" in h or "country" in h or "city" in h:
            col_map["destination"] = i
        elif "departure" in h or ("depart" in h and "date" in h):
            col_map["departure"] = i
        elif "return" in h:
            col_map["return"] = i
        elif "sponsor" in h or "committee" in h or "organization" in h:
            col_map["sponsor"] = i
        elif "total" in h:
            col_map["total_cost"] = i
        elif "per diem" in h:
            col_map["per_diem"] = i
        elif "transport" in h:
            col_map["transport"] = i
    return col_map


def extract_trips_from_pdf(pdf_path):
    """Extract travel records from a PDF using pdfplumber."""
    trips = []
    col_map = {}

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        if not row or all(not c for c in row):
                            continue

                        # Detect header row
                        if is_header_row(row):
                            col_map = map_columns(row)
                            continue

                        if not col_map:
                            continue

                        # Extract fields using column map
                        def cell(key):
                            idx = col_map.get(key)
                            if idx is not None and idx < len(row):
                                return (row[idx] or "").strip()
                            return ""

                        name = cell("name")
                        dest = cell("destination")
                        dep = cell("departure")
                        ret = cell("return")
                        sponsor = cell("sponsor")
                        total = cell("total_cost")

                        # If no total column, sum per_diem + transport
                        if not total:
                            pd_cost = parse_cost(cell("per_diem"))
                            tr_cost = parse_cost(cell("transport"))
                            if pd_cost or tr_cost:
                                total = str(pd_cost + tr_cost)

                        # Skip rows with no meaningful data
                        if not name and not dest:
                            continue

                        trip = {
                            "traveler_name": name,
                            "destination_country": dest,
                            "departure_date": parse_date(dep),
                            "return_date": parse_date(ret),
                            "sponsor": sponsor,
                            "total_cost": parse_cost(total),
                            "currency": "USD",
                        }
                        trips.append(trip)
    except Exception as e:
        print(f"    Error parsing PDF: {e}")

    return trips


# ---------------------------------------------------------------------------
# Member matching
# ---------------------------------------------------------------------------

def build_member_lookup(members):
    """Build normalized last-name -> [members] lookup for House members."""
    by_last = {}
    for m in members:
        if (m.get("chamber", "") or "").lower() != "house":
            continue
        parts = m.get("name", "").split()
        if not parts:
            continue
        last = normalize_name(parts[-1])
        by_last.setdefault(last, []).append(m)
    return by_last


def match_member(traveler_name, by_last):
    """Match a traveler name from PDF to a member. Returns bioguide_id or None."""
    norm = normalize_name(traveler_name)
    words = norm.split()
    if not words:
        return None

    # Try last word as last name
    last = words[-1]
    candidates = by_last.get(last, [])

    if len(candidates) == 1:
        return candidates[0]["id"]
    elif len(candidates) > 1 and len(words) > 1:
        first = words[0]
        for c in candidates:
            c_first = normalize_name(c["name"].split()[0])
            if c_first == first or (len(first) >= 3 and len(c_first) >= 3 and
                                     c_first.startswith(first[:3])):
                return c["id"]
        return candidates[0]["id"]

    return None


# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------

def supabase_upsert_travel(bid, trips):
    """Upsert travel records to Supabase."""
    if not SUPABASE_URL or not SUPABASE_KEY or not trips:
        return 0

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }

    rows = []
    for t in trips:
        rows.append({
            "bioguide_id": bid,
            "destination_country": t.get("destination_country", ""),
            "departure_date": t.get("departure_date") or None,
            "return_date": t.get("return_date") or None,
            "sponsor": t.get("sponsor", ""),
            "total_cost": t.get("total_cost", 0),
            "currency": t.get("currency", "USD"),
            "source": "house_clerk",
        })

    success = 0
    for i in range(0, len(rows), 100):
        chunk = rows[i:i + 100]
        try:
            r = requests.post(
                f"{SUPABASE_URL}/rest/v1/travel",
                headers=headers,
                json=chunk,
                timeout=30,
            )
            if r.status_code in (200, 201):
                success += len(chunk)
            else:
                print(f"    Supabase travel batch {i}: "
                      f"{r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"    Supabase travel error: {e}")

    return success


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("CongressWatch — House Foreign Travel PDF Scraper")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    members = load_json(MEMBERS_PATH, [])
    if not members:
        print("[FATAL] data/members.json not found or empty.")
        sys.exit(1)

    house = [m for m in members if (m.get("chamber", "") or "").lower() == "house"]
    print(f"Loaded {len(house)} House members from {len(members)} total")

    by_last = build_member_lookup(members)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    # Discover PDFs
    pdf_links = discover_pdf_links(session)
    if not pdf_links:
        print("[WARN] No PDF links found. The page format may have changed.")
        sys.exit(0)

    # Download and parse each PDF
    stats = {
        "pdfs_parsed": 0,
        "trips_extracted": 0,
        "trips_matched": 0,
        "members_updated": 0,
        "errors": 0,
        "supabase_upserted": 0,
    }

    # bioguide_id -> [trip dicts]
    all_trips = {}

    for pdf_url in pdf_links:
        print(f"\n  Downloading: {pdf_url.split('/')[-1]}")
        time.sleep(REQUEST_DELAY)

        try:
            resp = session.get(pdf_url, timeout=60)
            if resp.status_code != 200:
                print(f"    HTTP {resp.status_code}")
                stats["errors"] += 1
                continue

            # Save to temp file for pdfplumber
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(resp.content)
                tmp_path = tmp.name

            trips = extract_trips_from_pdf(tmp_path)
            os.unlink(tmp_path)

            print(f"    Extracted {len(trips)} trip records")
            stats["pdfs_parsed"] += 1
            stats["trips_extracted"] += len(trips)

            # Match to members
            for trip in trips:
                bid = match_member(trip["traveler_name"], by_last)
                if bid:
                    all_trips.setdefault(bid, []).append({
                        "destination_country": trip["destination_country"],
                        "departure_date": trip["departure_date"],
                        "return_date": trip["return_date"],
                        "sponsor": trip["sponsor"],
                        "total_cost": trip["total_cost"],
                        "currency": trip["currency"],
                    })
                    stats["trips_matched"] += 1

        except Exception as e:
            print(f"    Error: {e}")
            stats["errors"] += 1

    # Save results
    print("\n--- Saving results ---")

    members_by_id = {m["id"]: m for m in members}

    for bid, trips in all_trips.items():
        detail_path = os.path.join(DETAILS_DIR, f"{bid}.json")
        detail = load_json(detail_path, {})

        # Dedup by (departure_date, destination_country)
        existing = detail.get("travel", [])
        seen = {(t.get("departure_date", ""), t.get("destination_country", ""))
                for t in existing}

        added = []
        for t in trips:
            key = (t.get("departure_date", ""), t.get("destination_country", ""))
            if key not in seen:
                added.append(t)
                seen.add(key)

        all_travel = existing + added
        detail["travel"] = all_travel
        detail["travel_updated"] = datetime.now(timezone.utc).isoformat()
        detail["travel_count"] = len(all_travel)
        save_json(detail_path, detail)

        if bid in members_by_id:
            members_by_id[bid]["travel_count"] = len(all_travel)

        count = supabase_upsert_travel(bid, added)
        stats["supabase_upserted"] += count
        stats["members_updated"] += 1

        name = members_by_id.get(bid, {}).get("name", bid)
        print(f"  {name}: +{len(added)} trips ({len(all_travel)} total)")

    save_json(MEMBERS_PATH, members)

    # Summary
    print("\n" + "=" * 60)
    print("HOUSE TRAVEL PDF SCRAPER — COMPLETE")
    print("=" * 60)
    print(f"PDFs parsed:           {stats['pdfs_parsed']}")
    print(f"Trips extracted:       {stats['trips_extracted']}")
    print(f"Trips matched:         {stats['trips_matched']}")
    print(f"Members updated:       {stats['members_updated']}")
    print(f"Errors:                {stats['errors']}")
    if SUPABASE_URL:
        print(f"Supabase upserted:     {stats['supabase_upserted']}")
    print(f"Finished: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
