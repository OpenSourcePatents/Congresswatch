#!/usr/bin/env python3
"""
fetch_bills.py v1 — CongressWatch Bill Similarity Engine
=========================================================
Fetches sponsored bills for all 538 members, runs TF-IDF cosine similarity
against known ALEC model legislation and other members' bills, flags donor
interest alignment, and writes results into each member's vault file.

Data sources:
  - Congress.gov API  (sponsored bills, co-sponsors)
  - LegiScan API      (bill text via getBillText, incremental via change_hash)

Output:
  - data/bills/all_bills.json     — central bill cache (text + hashes + vectors)
  - data/details/{bid}.json       — updated with bills[] array per member

Run:
  python fetch_bills.py

Env vars required:
  CONGRESS_API_KEY
  LEGISCAN_API_KEY
"""

import json
import os
import sys
import time
import hashlib
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Path setup so utils imports work from repo root
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetch_bills.utils.api_clients import (
    get_member_sponsored_bills,
    get_bill_cosponsors,
    get_congress_bill_text_url,
    fetch_congress_text,
    legiscan_search_bill,
    legiscan_get_text_for_bill,
    legiscan_search_bill,
)
from fetch_bills.utils.text_processor import clean_bill_text, text_hash, extract_keywords
from fetch_bills.utils.similarity import SimilarityEngine
from fetch_bills.utils.donor_matcher import (
    get_member_donor_industries,
    match_donor_interests,
    score_donor_alignment,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
MEMBERS_PATH   = os.path.join(BASE_DIR, "data", "members.json")
DETAILS_DIR    = os.path.join(BASE_DIR, "data", "details")
BILLS_CACHE    = os.path.join(BASE_DIR, "data", "bills", "all_bills.json")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CURRENT_CONGRESS = 119
MAX_BILLS_PER_MEMBER = 10   # sponsored bills to analyze per member
LEGISCAN_QUERY_BUDGET = 200  # max LegiScan text fetches per run (protect quota)
SIMILARITY_THRESHOLD = 0.80

# ---------------------------------------------------------------------------
# Cache helpers
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


def load_detail(bioguide_id):
    path = os.path.join(DETAILS_DIR, f"{bioguide_id}.json")
    return load_json(path, {})


def save_detail(bioguide_id, data):
    path = os.path.join(DETAILS_DIR, f"{bioguide_id}.json")
    os.makedirs(DETAILS_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Bill text fetching (Congress.gov → LegiScan fallback)
# ---------------------------------------------------------------------------

def fetch_bill_text(bill_stub: dict, legiscan_budget: list) -> str:
    """
    Try Congress.gov text first. Fall back to LegiScan search if empty.
    legiscan_budget is a mutable [remaining_calls] list to track quota.
    Returns raw text string.
    """
    congress = bill_stub.get("congress", CURRENT_CONGRESS)
    bill_type = bill_stub.get("type", "")
    number = bill_stub.get("number", "")
    title = bill_stub.get("title", "")

    # --- Congress.gov text ---
    if congress and bill_type and number:
        text_url = get_congress_bill_text_url(congress, bill_type, number)
        if text_url:
            text = fetch_congress_text(text_url)
            if text and len(text) > 200:
                return text

    # --- LegiScan fallback ---
    if legiscan_budget[0] <= 0:
        return ""

    query = title[:80] if title else f"{bill_type}{number}"
    results = legiscan_search_bill(query, state="US", year=2)
    if not results:
        return ""

    # Pick best match by title similarity (simple prefix check)
    best = None
    for r in results[:3]:
        if r.get("legiscan_id"):
            best = r
            break

    if not best:
        return ""

    legiscan_budget[0] -= 1
    text = legiscan_get_text_for_bill(best["legiscan_id"])
    return text or ""


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("CongressWatch Bill Similarity Engine v1")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    # Load members
    members = load_json(MEMBERS_PATH, [])
    if not members:
        print("[FATAL] data/members.json not found or empty. Run fetch_members first.")
        sys.exit(1)
    print(f"Loaded {len(members)} members")

    # Load bills cache (incremental — only fetch changed bills)
    bills_cache = load_json(BILLS_CACHE, {})
    print(f"Bills cache: {len(bills_cache)} existing bills")

    # Build similarity engine from existing cache
    engine = SimilarityEngine()
    engine.load_corpus(bills_cache)

    # Track LegiScan quota
    legiscan_budget = [LEGISCAN_QUERY_BUDGET]

    # Stats
    stats = {
        "members_processed": 0,
        "bills_fetched": 0,
        "bills_cached": 0,
        "alec_matches": 0,
        "donor_matches": 0,
        "co_author_pairs": 0,
        "legiscan_calls": 0,
        "errors": 0,
    }

    # ---------------------------------------------------------------------------
    # Phase 1: Fetch all sponsored bills + text (incremental)
    # ---------------------------------------------------------------------------
    print("\n--- Phase 1: Fetching sponsored bills ---")

    member_bills_map = {}  # bioguide_id -> [bill_stubs with text]

    for i, member in enumerate(members):
        bid = member.get("id") or member.get("bioguide_id", "")
        name = member.get("name", "unknown")
        if not bid:
            continue

        print(f"  [{i+1}/{len(members)}] {name} ({bid})")

        # Fetch bill stubs from Congress.gov
        stubs = get_member_sponsored_bills(bid, congress=CURRENT_CONGRESS, limit=MAX_BILLS_PER_MEMBER)
        if not stubs:
            member_bills_map[bid] = []
            continue

        enriched = []
        for stub in stubs:
            bill_id = stub["bill_id"]

            # Check cache — skip if text unchanged
            cached = bills_cache.get(bill_id, {})
            raw_text = cached.get("raw_text", "")
            cached_hash = cached.get("text_hash", "")

            if not raw_text:
                # Fetch fresh text
                raw_text = fetch_bill_text(stub, legiscan_budget)
                stats["bills_fetched"] += 1

                if raw_text:
                    new_hash = text_hash(raw_text)
                    cleaned = clean_bill_text(raw_text)
                    bills_cache[bill_id] = {
                        "bill_id":      bill_id,
                        "title":        stub["title"],
                        "type":         stub["type"],
                        "number":       stub["number"],
                        "congress":     stub["congress"],
                        "url":          stub["url"],
                        "introduced_date": stub.get("introduced_date", ""),
                        "latest_action": stub.get("latest_action", ""),
                        "raw_text":     raw_text,
                        "cleaned_text": cleaned,
                        "text_hash":    new_hash,
                        "keywords":     extract_keywords(cleaned),
                        "sponsor_id":   bid,
                        "cached_at":    datetime.now(timezone.utc).isoformat(),
                    }
                    engine.add_bill(bill_id, cleaned)
            else:
                stats["bills_cached"] += 1

            enriched.append({**stub, "has_text": bool(raw_text)})

        member_bills_map[bid] = enriched
        stats["members_processed"] += 1

        # Save bills cache periodically
        if i % 50 == 0 and i > 0:
            print(f"    [CACHE] Saving bills cache ({len(bills_cache)} bills)...")
            save_json(BILLS_CACHE, bills_cache)

    # Final cache save
    print(f"\n[CACHE] Saving bills cache ({len(bills_cache)} bills)...")
    save_json(BILLS_CACHE, bills_cache)
    stats["legiscan_calls"] = LEGISCAN_QUERY_BUDGET - legiscan_budget[0]

    # ---------------------------------------------------------------------------
    # Phase 2: Run similarity analysis + write vault files
    # ---------------------------------------------------------------------------
    print("\n--- Phase 2: Similarity analysis + vault update ---")

    # Build co-author index: bill_id -> [cosponsor bioguide IDs]
    # (fetch on demand to avoid hammering API for all 538*10 bills)
    cosponsor_cache = {}

    for i, member in enumerate(members):
        bid = member.get("id") or member.get("bioguide_id", "")
        name = member.get("name", "unknown")
        if not bid:
            continue

        print(f"  [{i+1}/{len(members)}] Analyzing {name}...")

        detail = load_detail(bid)
        stubs = member_bills_map.get(bid, [])
        donor_industries = get_member_donor_industries(detail)

        analyzed_bills = []

        for stub in stubs:
            bill_id = stub["bill_id"]
            cached = bills_cache.get(bill_id, {})
            cleaned_text = cached.get("cleaned_text", "")
            title = cached.get("title", stub.get("title", ""))

            # Run similarity
            sim_result = engine.analyze_bill(bill_id, cleaned_text)

            # Donor interest match
            donor_result = match_donor_interests(cleaned_text, title, donor_industries)
            if donor_result["match"]:
                stats["donor_matches"] += 1

            # ALEC match
            alec_match = sim_result.get("alec_match")
            if alec_match:
                stats["alec_matches"] += 1

            # Co-author matches — find which similar bills are by other members
            co_author_matches = []
            for sim_bill in sim_result.get("similar_bills", []):
                sim_bill_id = sim_bill["bill_id"]
                sim_cached = bills_cache.get(sim_bill_id, {})
                other_sponsor = sim_cached.get("sponsor_id", "")
                if other_sponsor and other_sponsor != bid:
                    co_author_matches.append({
                        "bill_id": sim_bill_id,
                        "sponsor_id": other_sponsor,
                        "similarity_score": sim_bill["score"],
                    })
                    stats["co_author_pairs"] += 1

            # Fetch co-sponsors from Congress.gov (cached)
            cosponsors = []
            congress = cached.get("congress", CURRENT_CONGRESS)
            bill_type = cached.get("type", "")
            number = cached.get("number", "")
            if congress and bill_type and number:
                cache_key = f"{congress}_{bill_type}_{number}"
                if cache_key not in cosponsor_cache:
                    cosponsor_cache[cache_key] = get_bill_cosponsors(congress, bill_type, number)
                cosponsors = cosponsor_cache[cache_key]

            # Build final bill record
            bill_record = {
                "bill_id":          bill_id,
                "title":            title,
                "type":             cached.get("type", stub.get("type", "")),
                "number":           cached.get("number", stub.get("number", "")),
                "congress":         congress,
                "introduced_date":  cached.get("introduced_date", stub.get("introduced_date", "")),
                "latest_action":    cached.get("latest_action", stub.get("latest_action", "")),
                "url":              cached.get("url", stub.get("url", "")),
                "keywords":         cached.get("keywords", []),
                "has_text":         bool(cleaned_text),
                "similarity_score": alec_match["similarity_score"] if alec_match else None,
                "match_type":       "alec_model" if alec_match else ("member_bill" if co_author_matches else None),
                "alec_match":       alec_match,
                "similar_member_bills": co_author_matches[:3],
                "cosponsors":       cosponsors[:10],
                "donor_interest":   {
                    "match":              donor_result["match"],
                    "matched_industries": donor_result["matched_industries"],
                    "keyword_hits":       donor_result["keyword_hits"],
                },
            }
            analyzed_bills.append(bill_record)

        # Compute donor alignment score
        donor_alignment_score = score_donor_alignment(analyzed_bills)

        # Write to vault
        detail["bills"] = analyzed_bills
        detail["bills_updated"] = datetime.now(timezone.utc).isoformat()
        detail["bills_count"] = len(analyzed_bills)
        detail["donor_alignment_score"] = donor_alignment_score
        detail["alec_match_count"] = sum(1 for b in analyzed_bills if b.get("alec_match"))
        detail["donor_match_count"] = sum(1 for b in analyzed_bills if b.get("donor_interest", {}).get("match"))

        save_detail(bid, detail)

    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("BILL SIMILARITY ENGINE — COMPLETE")
    print("=" * 60)
    print(f"Members processed:    {stats['members_processed']}")
    print(f"Bills fetched (new):  {stats['bills_fetched']}")
    print(f"Bills from cache:     {stats['bills_cached']}")
    print(f"ALEC matches:         {stats['alec_matches']}")
    print(f"Donor interest hits:  {stats['donor_matches']}")
    print(f"Co-author pairs:      {stats['co_author_pairs']}")
    print(f"LegiScan calls used:  {stats['legiscan_calls']} / {LEGISCAN_QUERY_BUDGET}")
    print(f"Errors:               {stats['errors']}")
    print(f"Bills cache size:     {len(bills_cache)}")
    print(f"Finished: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)


if __name__ == "__main__":
    main()