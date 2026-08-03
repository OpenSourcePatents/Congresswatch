#!/usr/bin/env python3
"""
recompute_scores.py — Final pipeline step: rescore every member.

Why this exists
---------------
compute_score() reads four inputs — votes, trades, travel, bills — that are
refreshed by four DIFFERENT cron jobs on four different schedules. It used to
be called only by fetch_finance.py (5am). So the moment the votes job (2am the
next day), the bills job (7am), the travel job (9am) or either trades job
(8am/10am) wrote new data, the stored `score` was computed from inputs that no
longer existed. The number on the site disagreed with the data on the site.

This script closes that gap: it runs AFTER every fetcher, reads whatever is on
disk right now, and rewrites `score` + `score_components` from it. Scoring is
pure (no network, no API keys), so it is cheap to run and safe to re-run.

Output:
  - data/details/{bioguide_id}.json — score, score_components (safe merge)
  - data/members.json               — score (leaderboard tier)
  - data/stats.json                 — members_with_scores, high_anomaly

Run:
  python recompute_scores.py
"""

import os
from datetime import datetime

from fetch_finance import (
    load_json,
    save_json,
    compute_score_components,
    OUTPUT_FILE,
    DETAILS_DIR,
)

# Positions that count as a missed vote — mirrors both compute_score_components()
# and the frontend's HIGH MISSED VOTE RATE flag (index.html fillVotes).
MISSED_POSITIONS = ("not voting", "absent", "")

STATS_FILE = "data/stats.json"


def main():
    members = load_json(OUTPUT_FILE, [])
    if not members:
        raise SystemExit("ABORT: data/members.json is missing or empty.")

    print(f"Rescoring {len(members)} members from current on-disk data")

    changed = 0
    missing_detail = 0
    now = datetime.now().isoformat()

    for m in members:
        bid = m.get("id") or m.get("bioguide_id")
        if not bid:
            continue

        detail_path = os.path.join(DETAILS_DIR, f"{bid}.json")
        if not os.path.exists(detail_path):
            missing_detail += 1
            continue

        # Guarded load — a corrupt detail file aborts rather than being
        # silently rescored to 0 and written back over good data.
        detail = load_json(detail_path, {})

        old = detail.get("score")
        components = compute_score_components(m, detail)
        score = min(sum(components.values()), 100)

        if old != score:
            name = m.get("name", bid)
            print(f"  {name} ({bid}): {old} -> {score}")
            changed += 1

        # Leaderboard promotion: these live in detail files only (bills/votes
        # pipelines never touch members.json), but the frontend grid reads them
        # from members.json — without this they can never render there.
        m["alec_match_count"] = detail.get("alec_match_count", 0) or 0
        votes = detail.get("votes") or []
        if votes:
            missed = sum(1 for v in votes
                         if (v.get("position") or "").lower() in MISSED_POSITIONS)
            pct = round(missed * 100.0 / len(votes), 1)
            detail["missed_votes_pct"] = pct
            m["missed_votes_pct"] = pct

        # Safe merge: only touch the score fields, leave every other
        # pipeline's data in the detail file untouched.
        detail["score"] = score
        detail["score_components"] = components
        detail["score_updated"] = now
        save_json(detail_path, detail)

        m["score"] = score

    save_json(OUTPUT_FILE, members)

    # stats.json derives from score, so it is stale for exactly the same
    # reason — rebuild it here rather than leaving fetch_finance's snapshot.
    stats = load_json(STATS_FILE, {})
    stats["total_members"] = len(members)
    stats["members_with_scores"] = sum(1 for m in members if (m.get("score") or 0) > 0)
    stats["high_anomaly"] = sum(1 for m in members if (m.get("score") or 0) >= 60)
    stats["last_updated"] = now
    save_json(STATS_FILE, stats)

    print("=" * 60)
    print(f"Members rescored:   {len(members)}")
    print(f"Scores changed:     {changed}")
    print(f"Detail file absent: {missing_detail}")
    print(f"With a score > 0:   {stats['members_with_scores']}")
    print(f"High anomaly (>=60):{stats['high_anomaly']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
