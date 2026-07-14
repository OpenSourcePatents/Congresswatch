#!/usr/bin/env python3
"""
build_aggregates.py — Publish corpus-wide trade and bill documents.

Why this exists
---------------
Trades and bills live only INSIDE per-member detail files. A consumer wanting
"every NVDA trade in Congress" had to fetch all ~540 detail files (~15 MB) and
flatten them itself on every request. That work is identical for every consumer
and changes once a day, so it belongs here — computed once by the pipeline,
served as one cacheable document from the CDN.

Output (both flat arrays, sorted newest-first):
  - data/trades.json — every trade record, with its member stamped on
  - data/bills.json  — every bill record, with its member stamped on

Field contract:
  Each element is the native trade/bill object EXACTLY as it appears in the
  detail file — no renames, no reshaping — plus five identity fields:
      bioguide_id, member_name, party, state, chamber

Note on trades: ~230 records are placeholders for scanned/paper PTR filings
that could not be parsed ({scanned_pdf: true, ptr_link, date_recieved} and
nothing else — no ticker, no date, no amount). They are published as-is rather
than dropped: they never match a ticker or date filter, dropping them would put
trades.json out of agreement with the detail files, and "this member's filings
are unreadable paper scans" is itself a disclosure fact. Filter on
`scanned_pdf` to exclude them.

Run:
  python build_aggregates.py
"""

import glob
import json
import os

MEMBERS_FILE = "data/members.json"
DETAILS_DIR = "data/details"
TRADES_FILE = "data/trades.json"
BILLS_FILE = "data/bills.json"

# Stamped onto every record so a flat consumer never needs a second lookup.
IDENTITY_FIELDS = ("bioguide_id", "member_name", "party", "state", "chamber")


def load_json(path, default):
    """Guarded load: a missing/empty file yields default; a corrupt existing
    file ABORTS the run so we never publish a partial view."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise SystemExit(f"ABORT: {path} exists but failed to parse ({e}). "
                         "Refusing to continue — fix or remove the file.")


def save_json(path, data):
    """Atomic write: temp file + os.replace so a crash never truncates data."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def identity_for(member):
    return {
        "bioguide_id": member.get("id") or member.get("bioguide_id") or "",
        "member_name": member.get("name") or "",
        "party": member.get("party") or "",
        "state": member.get("state") or "",
        "chamber": member.get("chamber") or "",
    }


def stamp(record, identity):
    """Native record first, identity second. Native field names are preserved
    exactly; a collision would mean the detail schema grew a field named e.g.
    'state', which we want to hear about loudly rather than silently clobber."""
    collisions = set(record) & set(IDENTITY_FIELDS)
    if collisions:
        raise SystemExit(
            f"ABORT: record already has identity field(s) {sorted(collisions)} — "
            "the detail schema changed and stamping would overwrite real data."
        )
    return {**record, **identity}


def main():
    members = load_json(MEMBERS_FILE, [])
    if not members:
        raise SystemExit("ABORT: data/members.json is missing or empty.")

    # bioguide_id -> identity. Detail files exist for members who have since
    # left (542 files vs 536 members), so fall back to the detail file's own
    # fields rather than dropping their history.
    identity_by_id = {}
    for m in members:
        ident = identity_for(m)
        if ident["bioguide_id"]:
            identity_by_id[ident["bioguide_id"]] = ident

    trades, bills = [], []
    orphans = 0
    scanned_placeholders = 0

    for path in sorted(glob.glob(os.path.join(DETAILS_DIR, "*.json"))):
        detail = load_json(path, {})
        if not detail:
            continue

        bid = os.path.splitext(os.path.basename(path))[0]
        identity = identity_by_id.get(bid)
        if identity is None:
            orphans += 1
            identity = identity_for({**detail, "id": bid})

        # Coverage is uneven — most members have no trades, a few have no
        # bills. .get(..., []) or [] handles both absent and explicit null.
        for t in (detail.get("trades") or []):
            if t.get("scanned_pdf"):
                scanned_placeholders += 1
            trades.append(stamp(t, identity))

        for b in (detail.get("bills") or []):
            bills.append(stamp(b, identity))

    # Newest-first so consumers can page without re-sorting. Records with no
    # date (the scanned-PDF placeholders) sort last, not first: "" < any date.
    trades.sort(key=lambda t: t.get("transaction_date") or "", reverse=True)
    bills.sort(key=lambda b: b.get("introduced_date") or "", reverse=True)

    save_json(TRADES_FILE, trades)
    save_json(BILLS_FILE, bills)

    print("=" * 60)
    print("BUILD AGGREGATES — COMPLETE")
    print("=" * 60)
    print(f"Trades published:      {len(trades)}  -> {TRADES_FILE}")
    print(f"  of which unparsed scanned-PDF placeholders: {scanned_placeholders}")
    print(f"Bills published:       {len(bills)}  -> {BILLS_FILE}")
    print(f"Detail files scanned:  {len(glob.glob(os.path.join(DETAILS_DIR, '*.json')))}")
    print(f"Former members (no members.json row): {orphans}")
    print("=" * 60)


if __name__ == "__main__":
    main()
