"""
seed_supabase.py — One-time Supabase seeder for CongressWatch
================================================================
Run once to seed Supabase from existing JSON data.

Usage:
  SUPABASE_URL=https://... SUPABASE_SERVICE_KEY=... python seed_supabase.py

Reads data/members.json and data/details/*.json, upserts everything to Supabase.
"""

import os
import json
import requests
from datetime import datetime

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

MEMBERS_FILE = "data/members.json"
DETAILS_DIR = "data/details"

if not SUPABASE_URL or not SUPABASE_KEY:
    print("ERROR: Set SUPABASE_URL and SUPABASE_SERVICE_KEY environment variables")
    exit(1)

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}
HEADERS_MINIMAL = {**HEADERS, "Prefer": "return=minimal"}


def upsert_batch(table, rows, chunk_size=50):
    success = 0
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i+chunk_size]
        try:
            r = requests.post(
                f"{SUPABASE_URL}/rest/v1/{table}",
                headers=HEADERS,
                json=chunk,
                timeout=30
            )
            if r.status_code in (200, 201):
                success += len(chunk)
            else:
                print(f"  {table} batch {i}: {r.status_code} {r.text[:300]}")
        except Exception as e:
            print(f"  {table} batch {i} error: {e}")
    return success


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


if __name__ == "__main__":
    print("=" * 60)
    print("CongressWatch Supabase Seeder")
    print(f"Target: {SUPABASE_URL}")
    print("=" * 60)

    # ── Step 1: Load and upsert members ──
    members = load_json(MEMBERS_FILE, [])
    if not members:
        print("No members.json found")
        exit(1)

    print(f"\nLoaded {len(members)} members from {MEMBERS_FILE}")

    member_rows = []
    for m in members:
        bid = m.get("id") or m.get("bioguide_id")
        if not bid:
            continue
        member_rows.append({
            "bioguide_id": bid,
            "name": m.get("name", ""),
            "party": m.get("party", "Unknown"),
            "state": m.get("state", ""),
            "district": m.get("district", ""),
            "chamber": m.get("chamber", "House"),
            "photo_url": m.get("photo_url", ""),
            "term_start": m.get("term_start", "2010-01-01"),
            "score": m.get("score", 0) or 0,
            "flags": m.get("flags", []),
            "total_raised": float(m.get("total_raised", 0) or 0),
            "pac_contributions": float(m.get("pac_contributions", 0) or 0),
            "individual_contributions": float(m.get("individual_contributions", 0) or 0),
            "corporate_insider_signals": m.get("corporate_insider_signals", 0) or 0,
            "edgar_status": m.get("edgar_status", "unresolved"),
            "edgar_cik": m.get("edgar_cik"),
            "fec_candidate_id": m.get("fec_candidate_id"),
            "data_updated": m.get("data_updated") or datetime.now().isoformat(),
            "congress_updated": datetime.now().isoformat()
        })

    count = upsert_batch("members", member_rows)
    print(f"Members upserted: {count}/{len(member_rows)}")

    # ── Step 2: Load detail files, upsert votes and bills ──
    total_votes = 0
    total_bills = 0
    detail_count = 0

    for m in members:
        bid = m.get("id") or m.get("bioguide_id")
        if not bid:
            continue

        detail = load_json(os.path.join(DETAILS_DIR, f"{bid}.json"), {})
        if not detail:
            continue

        detail_count += 1

        # Votes
        votes = detail.get("votes", [])
        if votes:
            vote_rows = []
            for v in votes:
                vote_url = v.get("url", "")
                vote_id = vote_url.split("/")[-1] if vote_url else f"{bid}_{v.get('date', '')}_{hash(v.get('bill', ''))}"
                vote_rows.append({
                    "bioguide_id": bid,
                    "vote_id": vote_id,
                    "chamber": v.get("chamber", ""),
                    "question": v.get("bill", ""),
                    "description": v.get("question_text", ""),
                    "date": v.get("date", ""),
                    "position": v.get("position", ""),
                    "result": v.get("result", ""),
                    "url": vote_url
                })
            c = upsert_batch("votes", vote_rows, chunk_size=100)
            total_votes += c

        # Bills
        bills = detail.get("bills", [])
        if bills:
            bill_rows = []
            for b in bills:
                alec = b.get("alec_match") or {}
                donor = b.get("donor_interest") or {}
                bill_rows.append({
                    "bioguide_id": bid,
                    "bill_id": b.get("bill_id", ""),
                    "bill_type": b.get("type", ""),
                    "bill_number": b.get("number", ""),
                    "title": b.get("title", ""),
                    "introduced_date": b.get("introduced_date") or None,
                    "latest_action": b.get("latest_action", ""),
                    "url": b.get("url", ""),
                    "alec_similarity_score": alec.get("similarity_score", 0) or 0,
                    "alec_matched_model": alec.get("matched_model", ""),
                    "alec_category": alec.get("category", ""),
                    "alec_source_url": alec.get("source_url", ""),
                    "donor_interest_match": donor.get("match", False),
                    "donor_interest_reason": ", ".join(donor.get("matched_industries", [])),
                })
            c = upsert_batch("bills", bill_rows)
            total_bills += c

        if detail_count % 50 == 0:
            print(f"  Processed {detail_count} detail files...")

    # ── Step 3: Update stats ──
    print("\nUpdating stats...")
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/update_stats",
            headers=HEADERS_MINIMAL,
            json={},
            timeout=15
        )
        if r.status_code in (200, 204):
            print("Stats updated via RPC")
        else:
            print(f"Stats RPC: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"Stats RPC error: {e}")

    # ── Summary ──
    print("\n" + "=" * 60)
    print("SEED COMPLETE")
    print("=" * 60)
    print(f"Members upserted:     {count}")
    print(f"Detail files read:    {detail_count}")
    print(f"Votes upserted:       {total_votes}")
    print(f"Bills upserted:       {total_bills}")
    print("=" * 60)
