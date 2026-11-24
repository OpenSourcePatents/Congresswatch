"""Reconcile the November 24 review with immutable, source-only main deltas.

Historical tool for this exact recovery, not a scheduled collector. Every
incoming changed field is classified before writing; raw observations remain
archived and bill calculations are never promoted from the legacy pipeline.
"""
import copy
import json
import subprocess
from collections import Counter
from pathlib import Path

BASE = "fc9ae162c75c03ea7f73dc1a0c29e41aa6bc7a30"
REVIEW = "b8b07ec2147b2bf6ae0dc4a1f028302bfef9e753"
UPSTREAM = "a3a41f05624ba3b2571289ad653c1f204f3295ce"
RAW = {
    "name", "party", "state", "district", "chamber", "photo_url", "term_start",
    "govtrack_id", "bioguide_id", "updated", "roster_updated",
    "total_raised", "total_raised_display", "pac_contributions", "individual_contributions",
    "total_spent", "cash_on_hand", "fec_candidate_id", "fec_cycle", "fec_committee_ids",
    "trades", "trades_updated", "trade_count", "latest_trade_date",
    "travel", "travel_updated", "travel_count", "top_donors_list", "top_donor_industries",
    "donors_updated", "donors_status", "finance_updated", "finance_status",
}
DERIVED = {"score", "score_components", "score_updated", "last_updated", "flags",
           "donor_alignment_score", "donor_match_count", "alec_match_count"}
BILLS = {"bills", "bills_updated", "bills_count", "bills_status", "bills_analysis_status"}
SEC = {"corporate_insider_signals", "edgar_cik", "edgar_status", "data_updated"}
VOTES = {"votes", "votes_updated", "votes_fail_count", "votes_status"}


def load(ref, path):
    return json.loads(subprocess.check_output(["git", "show", ref + ":" + path]))


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def reconcile(old, base, incoming, counts):
    result = copy.deepcopy(old)
    changed = {k for k in base.keys() | incoming.keys() if base.get(k) != incoming.get(k)}
    unknown = changed - RAW - DERIVED - BILLS - SEC - VOTES
    if unknown:
        raise ValueError(f"{old['id']}: unclassified incoming fields: {unknown}")
    # Archive every incoming changed field, including superseded derived fields.
    if changed:
        result.setdefault("source_reconciliation_history", []).append({
            "source_snapshot": UPSTREAM, "prior_upstream_snapshot": BASE,
            "publication_status": "source_observation_only",
            "changed_fields": {k: incoming.get(k) for k in sorted(changed)}})
    for key in changed & RAW:
        result[key] = copy.deepcopy(incoming.get(key))
        counts[key] += 1
    if changed & VOTES:
        if incoming.get("votes") != base.get("votes") or incoming.get("votes_status") != "fetch_failed":
            raise ValueError(f"{old['id']}: changed votes need independent review")
        result["votes_checked"] = incoming.get("votes_updated")
        result["votes_fail_count"] = incoming.get("votes_fail_count")
        result["votes_status"] = "fetch_failed"
        # A failed attempt is not a successful collection.
        result["votes_updated"] = old.get("votes_updated")
    if changed & SEC:
        result["upstream_sec_observation"] = {
            "source_snapshot": UPSTREAM, "candidate_only": True,
            "fields": {k: incoming.get(k) for k in sorted(SEC)}}
        if incoming.get("edgar_status") == "query_failed":
            result["edgar_data_status"] = "stale"
        else:
            for key in ("edgar_cik", "corporate_insider_signals"):
                if key in incoming:
                    result[key] = incoming[key]
            result["edgar_status"] = "unverified_auto"
            result["edgar_identity_status"] = "unverified"
        result["edgar_checked"] = incoming.get("data_updated")
    if changed & BILLS:
        result.setdefault("bill_history", []).append({
            "source_snapshot": UPSTREAM, "publication_status": "legacy_archive_only",
            "original_fields": {k: incoming.get(k) for k in sorted(BILLS)}})
    # Every known manual correction survives unchanged if its upstream source did
    # not change. Explicitly refuse a collision rather than overwriting review.
    for key in ("trades", "travel"):
        if key in changed and old.get(key) != base.get(key):
            raise ValueError(f"{old['id']}: simultaneous raw/manual {key} edit needs review")
    return result


def main():
    review = subprocess.check_output(["git", "rev-parse", REVIEW], text=True).strip()
    rosters = [load(ref, "data/members.json") for ref in (review, BASE, UPSTREAM)]
    maps = [{m["id"]: m for m in roster} for roster in rosters]
    if not maps[0].keys() == maps[1].keys() == maps[2].keys():
        raise ValueError("roster identity drift")
    outputs, counts = {}, Counter()
    members = []
    for m in rosters[0]:
        bid = m["id"]
        path = f"data/details/{bid}.json"
        old, base, incoming = [load(ref, path) for ref in (review, BASE, UPSTREAM)]
        if not old["id"] == base["id"] == incoming["id"] == bid:
            raise ValueError("detail identity mismatch")
        outputs[path] = reconcile(old, base, incoming, counts)
        members.append(reconcile(m, maps[1][bid], maps[2][bid], Counter()))
        for key in RAW:
            # A raw field changed by main must exactly match its source.
            if base.get(key) != incoming.get(key):
                assert outputs[path].get(key) == incoming.get(key), (bid, key)
        assert outputs[path].get("votes") == old.get("votes"), bid
        assert outputs[path].get("bills") == old.get("bills"), bid
    outputs["data/members.json"] = members
    # Existing aggregate conflicts are deliberately resolved by regeneration.
    outputs["data/stats.json"] = load(review, "data/stats.json")
    for path, value in outputs.items():
        save(path, value)
    report = {"review": review, "prior_upstream": BASE, "upstream": UPSTREAM,
              "members": len(members), "raw_field_updates": dict(counts),
              "latest_raw_fields_verified": True, "held_vote_records_verified": True,
              "legacy_bill_calculations_archived_only": True}
    save("benchmarks/nov24-source-reconciliation.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
