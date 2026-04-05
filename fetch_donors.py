#!/usr/bin/env python3
"""
fetch_donors.py — FEC Itemized Individual Donor Fetcher
=========================================================
Pulls top individual contributions from the FEC API
(Schedule A / itemized individual contributions) for each member.

Source: https://api.open.fec.gov/v1/schedules/schedule_a/

Output:
  - data/details/{bioguide_id}.json — top_donors_list[] (safe merge)
  - data/members.json — (unchanged, donors are detail-only)

Supabase table schema — run in SQL Editor before first use:

-- CREATE TABLE IF NOT EXISTS public.donors (
--   id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
--   bioguide_id TEXT NOT NULL,
--   contributor_name TEXT,
--   contributor_employer TEXT,
--   contributor_occupation TEXT,
--   amount NUMERIC(12,2),
--   date DATE,
--   source TEXT DEFAULT 'fec_schedule_a',
--   created_at TIMESTAMPTZ DEFAULT now(),
--   UNIQUE(bioguide_id, contributor_name, amount, date)
-- );
-- CREATE INDEX IF NOT EXISTS idx_donors_member ON public.donors(bioguide_id);
-- ALTER TABLE public.donors ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY "donors_read" ON public.donors FOR SELECT TO anon USING (true);
-- CREATE POLICY "donors_service_all" ON public.donors FOR ALL TO service_role USING (true) WITH CHECK (true);

Env vars:
  FEC_API_KEY (optional — falls back to DEMO_KEY)
  SUPABASE_URL (optional)
  SUPABASE_SERVICE_KEY (optional)

Run:
  python fetch_donors.py
"""

import json
import os
import sys
import time
import requests
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
MEMBERS_PATH = os.path.join(BASE_DIR, "data", "members.json")
DETAILS_DIR  = os.path.join(BASE_DIR, "data", "details")

FEC_BASE    = "https://api.open.fec.gov/v1"
FEC_API_KEY = os.environ.get("FEC_API_KEY", "DEMO_KEY")
PER_PAGE    = 20         # top N donors per member
DELAY       = 0.5        # seconds between API calls
MAX_RETRIES = 3
BACKOFF     = 2          # seconds, doubles each retry

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


# ---------------------------------------------------------------------------
# FEC API
# ---------------------------------------------------------------------------

def fetch_top_donors(candidate_id):
    """
    Fetch top individual contributions for a candidate from FEC Schedule A.
    Returns list of donor dicts sorted by amount descending.
    """
    url = f"{FEC_BASE}/schedules/schedule_a/"
    params = {
        "candidate_id": candidate_id,
        "sort": "-contribution_receipt_amount",
        "per_page": PER_PAGE,
        "api_key": FEC_API_KEY,
    }

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=30)

            if resp.status_code == 429:
                wait = BACKOFF * (2 ** attempt)
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue

            if resp.status_code != 200:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(BACKOFF)
                    continue
                return []

            data = resp.json()
            results = data.get("results", [])

            donors = []
            for r in results:
                donors.append({
                    "name": r.get("contributor_name", ""),
                    "employer": r.get("contributor_employer", ""),
                    "occupation": r.get("contributor_occupation", ""),
                    "amount": r.get("contribution_receipt_amount", 0),
                    "date": r.get("contribution_receipt_date", ""),
                })

            return donors

        except requests.exceptions.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF * (2 ** attempt))
            else:
                print(f"    Request failed: {e}")
                return []

    return []


# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------

def supabase_upsert_donors(bid, donors):
    """Upsert donors to Supabase donors table."""
    if not SUPABASE_URL or not SUPABASE_KEY or not donors:
        return 0

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }

    rows = []
    for d in donors:
        rows.append({
            "bioguide_id": bid,
            "contributor_name": d.get("name", ""),
            "contributor_employer": d.get("employer", ""),
            "contributor_occupation": d.get("occupation", ""),
            "amount": d.get("amount", 0),
            "date": d.get("date") or None,
            "source": "fec_schedule_a",
        })

    success = 0
    for i in range(0, len(rows), 100):
        chunk = rows[i:i + 100]
        try:
            r = requests.post(
                f"{SUPABASE_URL}/rest/v1/donors",
                headers=headers,
                json=chunk,
                timeout=30,
            )
            if r.status_code in (200, 201):
                success += len(chunk)
            else:
                print(f"    Supabase donors batch {i}: "
                      f"{r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"    Supabase donors error: {e}")

    return success


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("CongressWatch — FEC Itemized Donor Fetcher")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print(f"API key: {'env' if FEC_API_KEY != 'DEMO_KEY' else 'DEMO_KEY'}")
    print("=" * 60)

    members = load_json(MEMBERS_PATH, [])
    if not members:
        print("[FATAL] data/members.json not found or empty.")
        sys.exit(1)

    # Filter to members with fec_candidate_id
    eligible = []
    for m in members:
        bid = m.get("id", "")
        detail = load_json(os.path.join(DETAILS_DIR, f"{bid}.json"), {})
        fec_id = detail.get("fec_candidate_id", "")
        if fec_id:
            eligible.append((m, detail, fec_id))

    print(f"Members with FEC candidate ID: {len(eligible)}/{len(members)}")

    if not eligible:
        print("[WARN] No members have fec_candidate_id. "
              "Run fetch_finance.py first.")
        sys.exit(0)

    stats = {
        "members_fetched": 0,
        "donors_found": 0,
        "members_updated": 0,
        "errors": 0,
        "supabase_upserted": 0,
    }

    for idx, (member, detail, fec_id) in enumerate(eligible):
        bid = member["id"]
        name = member.get("name", bid)
        print(f"  [{idx+1}/{len(eligible)}] {name} ({fec_id})")

        time.sleep(DELAY)

        try:
            donors = fetch_top_donors(fec_id)
            stats["members_fetched"] += 1

            if not donors:
                continue

            stats["donors_found"] += len(donors)

            # Safe merge into detail file
            detail_path = os.path.join(DETAILS_DIR, f"{bid}.json")
            detail = load_json(detail_path, {})
            detail["top_donors_list"] = donors
            detail["donors_updated"] = datetime.now(timezone.utc).isoformat()
            save_json(detail_path, detail)

            # Supabase upsert
            count = supabase_upsert_donors(bid, donors)
            stats["supabase_upserted"] += count
            stats["members_updated"] += 1

            print(f"    {len(donors)} donors (top: "
                  f"${donors[0]['amount']:,.0f} — {donors[0]['name'][:30]})")

        except Exception as e:
            print(f"    Error: {e}")
            stats["errors"] += 1

    # Summary
    print("\n" + "=" * 60)
    print("FEC DONOR FETCHER — COMPLETE")
    print("=" * 60)
    print(f"Members fetched:       {stats['members_fetched']}")
    print(f"Total donors found:    {stats['donors_found']}")
    print(f"Members updated:       {stats['members_updated']}")
    print(f"Errors:                {stats['errors']}")
    if SUPABASE_URL:
        print(f"Supabase upserted:     {stats['supabase_upserted']}")
    print(f"Finished: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
