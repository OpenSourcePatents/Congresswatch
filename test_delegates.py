"""
CongressWatch — non-voting delegate classification test.

Run manually (repo convention — not a pytest suite, though pytest collects it):

    python test_delegates.py

Guards the attendance signal against two specific regressions:

1. The substring trap. "Virgin Islands" is a substring of neither but collides
   with "Virginia" and "West Virginia" under any `in`/`includes`/`startswith`
   style match. A substring implementation would silently exempt every member
   from both Virginias from attendance scoring — ~13 members quietly losing a
   penalty with no visible error anywhere.

2. Data-based detection. Delegates' upstream vote records are inconsistent:
   as of 2026-08-03 all six have 20 stored vote records, but four read 0.0%
   missed and two read 100%. Any classifier keyed off the vote data itself
   would split them into two groups. Classification must be identity-based.
"""

import json
import os
import sys

from fetch_finance import (
    NON_VOTING_STATES,
    is_non_voting_delegate,
    compute_score_components,
)

MEMBERS_FILE = "data/members.json"

# The complete set of non-voting House seats in the 119th Congress:
# 5 delegates + Puerto Rico's Resident Commissioner.
EXPECTED_NON_VOTING = {
    "N000147",  # District of Columbia
    "H001103",  # Puerto Rico (Resident Commissioner)
    "P000610",  # Virgin Islands
    "M001219",  # Guam
    "R000600",  # American Samoa
    "K000404",  # Northern Mariana Islands
}

failures = []


def check(condition, message):
    if condition:
        print(f"  PASS  {message}")
    else:
        print(f"  FAIL  {message}")
        failures.append(message)


def main():
    if not os.path.exists(MEMBERS_FILE):
        sys.exit(f"ABORT: {MEMBERS_FILE} not found — run from the repo root.")

    with open(MEMBERS_FILE) as f:
        members = json.load(f)

    print(f"Loaded {len(members)} members from {MEMBERS_FILE}\n")

    # ── 1. Exactly the six expected IDs are classified non-voting ────────────
    print("Non-voting classification over the live roster:")
    actual = {m["id"] for m in members if is_non_voting_delegate(m)}

    check(actual == EXPECTED_NON_VOTING,
          f"exactly the 6 expected delegates classified non-voting "
          f"(got {len(actual)})")
    for missing in sorted(EXPECTED_NON_VOTING - actual):
        check(False, f"{missing} SHOULD be non-voting but is not")
    for extra in sorted(actual - EXPECTED_NON_VOTING):
        check(False, f"{extra} should NOT be non-voting but is")

    # ── 2. The Virginia substring trap ──────────────────────────────────────
    print("\nSubstring collision guard:")
    for state in ("Virginia", "West Virginia"):
        group = [m for m in members if m.get("state") == state]
        check(len(group) > 0, f"{state} members present in roster "
                              f"({len(group)}) - test is meaningful")
        wrongly = [m["id"] for m in group if is_non_voting_delegate(m)]
        check(not wrongly,
              f"no {state} member classified non-voting "
              f"(would-be false positives: {wrongly or 'none'})")

    check("Virginia" not in NON_VOTING_STATES,
          "'Virginia' is not in NON_VOTING_STATES")
    check("West Virginia" not in NON_VOTING_STATES,
          "'West Virginia' is not in NON_VOTING_STATES")
    check("Virgin Islands" in NON_VOTING_STATES,
          "'Virgin Islands' IS in NON_VOTING_STATES")

    # ── 3. Chamber is part of the identity, not just state ──────────────────
    print("\nChamber guard:")
    check(not is_non_voting_delegate({"state": "Virgin Islands",
                                      "chamber": "Senate"}),
          "a hypothetical Senate seat in a territory is NOT a House delegate")
    check(is_non_voting_delegate({"state": "Guam", "chamber": "House"}),
          "state + House together do classify as non-voting")
    check(not is_non_voting_delegate({}), "empty member does not crash/classify")
    check(not is_non_voting_delegate(None), "None does not crash/classify")

    # ── 4. Attendance is exempt regardless of what the vote data says ───────
    print("\nAttendance exemption (identity-based, not data-based):")
    # Both shapes seen in production for delegates: all-missed and none-missed.
    all_missed = {"votes": [{"position": "Not Voting"} for _ in range(20)]}
    none_missed = {"votes": [{"position": "Yea"} for _ in range(20)]}
    delegate = {"state": "Virgin Islands", "chamber": "House"}
    voting_rep = {"state": "Virginia", "chamber": "House"}

    check(compute_score_components(delegate, all_missed)["attendance"] == 0,
          "delegate with 100% missed scores 0 attendance")
    check(compute_score_components(delegate, none_missed)["attendance"] == 0,
          "delegate with 0% missed scores 0 attendance")
    check(compute_score_components(voting_rep, all_missed)["attendance"] == 5,
          "voting member with 100% missed still scores the full 5 penalty")

    # ── 5. Published data reflects the exemption ────────────────────────────
    print("\nPublished data in members.json:")
    for bid in sorted(EXPECTED_NON_VOTING):
        m = next((x for x in members if x["id"] == bid), None)
        if m is None:
            check(False, f"{bid} present in members.json")
            continue
        check(m.get("missed_votes_pct") is None,
              f"{bid} ({m.get('state')}) publishes no missed_votes_pct")
        check(m.get("non_voting_delegate") is True,
              f"{bid} carries non_voting_delegate=True for the frontend")

        detail_path = os.path.join("data", "details", f"{bid}.json")
        if os.path.exists(detail_path):
            with open(detail_path) as f:
                detail = json.load(f)
            att = (detail.get("score_components") or {}).get("attendance")
            check(att == 0, f"{bid} attendance component is 0 (got {att})")

    print()
    print("=" * 60)
    if failures:
        print(f"FAILED — {len(failures)} assertion(s):")
        for f_ in failures:
            print(f"  - {f_}")
        print("=" * 60)
        sys.exit(1)
    print("All delegate classification checks passed.")
    print("=" * 60)


if __name__ == "__main__":
    main()
