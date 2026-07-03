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


# ---------------------------------------------------------------------------
# Donor employer/occupation -> industry classification
# Labels MUST match INDUSTRY_KEYWORDS keys in fetch_bills/utils/donor_matcher.py
# so the bills pipeline's donor-alignment matching can consume them.
# ---------------------------------------------------------------------------

EMPLOYER_INDUSTRY_KEYWORDS = {
    "Oil & Gas": ["exxon", "chevron", "shell", "conocophillips", "halliburton",
                  "petroleum", "oil ", " oil", "energy", "pipeline", "drilling",
                  "gas company", "marathon", "valero", "occidental"],
    "Pharmaceuticals": ["pfizer", "merck", "johnson & johnson", "abbvie", "amgen",
                        "eli lilly", "novartis", "astrazeneca", "pharma",
                        "biotech", "genentech", "gilead", "bristol"],
    "Finance & Banking": ["goldman", "morgan", "citigroup", "citibank", "wells fargo",
                          "bank", "capital", "investment", "securities", "hedge",
                          "private equity", "blackstone", "blackrock", "fidelity",
                          "charles schwab", "venture", "financial"],
    "Defense & Military": ["lockheed", "raytheon", "boeing", "northrop", "general dynamics",
                           "defense", "bae systems", "l3harris", "military", "aerospace"],
    "Technology": ["google", "alphabet", "microsoft", "apple", "amazon", "meta",
                   "facebook", "netflix", "oracle", "salesforce", "software",
                   "tech", "nvidia", "intel", "cisco", "ibm", "engineer"],
    "Real Estate": ["real estate", "realty", "properties", "developer",
                    "development", "construction", "homebuilder", "realtor"],
    "Agriculture": ["farm", "agri", "cargill", "monsanto", "ranch", "dairy",
                    "poultry", "cattle", "grower"],
    "Healthcare": ["hospital", "health", "medical", "clinic", "physician",
                   "doctor", "nurse", "dentist", "unitedhealth", "kaiser",
                   "anthem", "cvs", "surgeon"],
    "Tobacco & Alcohol": ["altria", "philip morris", "reynolds", "tobacco",
                          "anheuser", "molson", "distill", "brewer", "wine ", "liquor"],
    "Firearms": ["smith & wesson", "sturm ruger", "firearm", "nra",
                 "gun ", "ammunition", "shooting sports"],
    "Insurance": ["insurance", "insurer", "aflac", "allstate", "state farm",
                  "geico", "progressive", "metlife", "prudential", "actuar"],
    "Telecommunications": ["at&t", "verizon", "comcast", "t-mobile", "telecom",
                           "charter communications", "broadband", "wireless"],
    "Mining & Natural Resources": ["mining", "minerals", "coal", "copper",
                                   "quarry", "timber", "lumber", "steel"],
    "Education": ["university", "college", "school", "professor",
                  "teacher", "educator", "academy"],
    "Labor & Unions": ["union", "afl-cio", "teamsters", "seiu", "afscme",
                       "uaw", "brotherhood of"],
}


def derive_top_donor_industries(donors, top_n=5):
    """Rank donor industries by total contribution amount using
    employer/occupation keyword matching. Returns list of industry labels."""
    totals = {}
    for d in donors:
        text = f"{d.get('employer','')} {d.get('occupation','')}".lower()
        if not text.strip():
            continue
        amount = float(d.get("amount") or 0)
        for industry, keywords in EMPLOYER_INDUSTRY_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                totals[industry] = totals.get(industry, 0) + max(amount, 1.0)
    ranked = sorted(totals.items(), key=lambda x: -x[1])
    return [industry for industry, _ in ranked[:top_n]]


# ---------------------------------------------------------------------------
# FEC API
# ---------------------------------------------------------------------------

def get_candidate_committees(candidate_id):
    """Resolve a candidate's authorized committee IDs. Schedule A filters by
    committee_id — passing candidate_id there returns HTTP 400 (verified),
    which is why this pipeline previously never stored a single donor."""
    url = f"{FEC_BASE}/candidate/{candidate_id}/committees/"
    params = {
        "api_key": FEC_API_KEY,
        "designation": ["P", "A"],   # principal + authorized
        "per_page": 10,
    }
    try:
        time.sleep(DELAY)
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            print(f"    Committee lookup {resp.status_code} for {candidate_id}")
            return []
        return [c.get("committee_id") for c in resp.json().get("results", [])
                if c.get("committee_id")]
    except requests.exceptions.RequestException as e:
        print(f"    Committee lookup failed: {e}")
        return []


def fetch_top_donors(committee_ids):
    """
    Fetch top itemized individual contributions to a candidate's committees
    from FEC Schedule A. Returns list of donor dicts sorted by amount desc.
    """
    url = f"{FEC_BASE}/schedules/schedule_a/"
    params = {
        "committee_id": committee_ids,
        "is_individual": "true",
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
            # Committee IDs are stable — cache them in the detail file
            committee_ids = detail.get("fec_committee_ids") or []
            if not committee_ids:
                committee_ids = get_candidate_committees(fec_id)

            if not committee_ids:
                print("    No authorized committees found")
                continue

            donors = fetch_top_donors(committee_ids)
            stats["members_fetched"] += 1

            if not donors:
                continue

            stats["donors_found"] += len(donors)

            # Safe merge into detail file
            detail_path = os.path.join(DETAILS_DIR, f"{bid}.json")
            detail = load_json(detail_path, {})
            detail["top_donors_list"] = donors
            detail["fec_committee_ids"] = committee_ids
            # Feeds the bills pipeline's donor-vote alignment signal
            detail["top_donor_industries"] = derive_top_donor_industries(donors)
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
