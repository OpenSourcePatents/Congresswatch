#!/usr/bin/env python3
"""
fetch_senate_travel.py — Senate privately-sponsored (Rule 35 / gift-rule) travel.

Source: the Secretary of the Senate's gift-rule disclosure site publishes a
bulk XML index of every travel filing (no auth, no consent gate, one GET):

    https://giftrule-disclosure.senate.gov/media/giftruledownloads/giftruledata.zip

Shape:  <GiftRule>
          <dbo.filer LastName FirstName>
            <dbo.Office OfficeName="LAST, FIRST">
              <dbo.Document ReportingYear BeginTravelDate EndTravelDate
                            DateReceived Pages>
                <dbo.Reports ReportTitle DocURL/>

Member-filed trips carry ReportTitle == "Member Reimbursed Travel"; staff
trips ("Employee Reimbursed Travel") are skipped, mirroring the House
pipeline's member-only scope.

Source limits (same shape as the House gift-travel index):
  - destination, sponsor and costs exist only inside the linked PDFs, which
    are scanned images — records store the document link + travel dates.
  - the bulk file is a rolling ~4-year retention window (HLOGA §546); this
    script only ever ADDS trips, so older ones survive in our data.

Output: data/details/{bid}.json travel[] (safe merge) + travel_count, and
data/members.json travel_count.
"""

import io
import json
import os
import re
import sys
import time
import tempfile
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

ZIP_URL = ("https://giftrule-disclosure.senate.gov"
           "/media/giftruledownloads/giftruledata.zip")

MEMBERS_PATH = "data/members.json"
DETAILS_DIR = "data/details"

HEADERS = {
    "User-Agent": "CongressWatch/1.0 (public-interest-research; "
                  "mailto:project.congress.watch@gmail.com)",
    "Accept-Encoding": "gzip, deflate",
}

MEMBER_REPORT_TITLE = "Member Reimbursed Travel"

DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# IO helpers (repo conventions: guarded loads, atomic writes)
# ---------------------------------------------------------------------------

def load_json_strict(path, default):
    """Missing/empty file -> default. An EXISTING non-empty file that fails
    to parse ABORTS — returning a default would wipe other pipelines' data."""
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    if not raw.strip():
        return default
    try:
        return json.loads(raw)
    except Exception as e:
        print(f"[FATAL] {path} exists but is not valid JSON ({e}). "
              f"Aborting to avoid clobbering data from other pipelines.")
        raise SystemExit(1)


def save_json(path, data):
    """Atomic write: temp file in the same directory, then os.replace."""
    dirname = os.path.dirname(path)
    os.makedirs(dirname, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_sen_travel_",
                                    suffix=".json", dir=dirname)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def normalize_name(name):
    """Lowercase, FOLD DIACRITICS (Luján -> lujan), strip suffixes/punct.
    The plain [^a-z] scrub used elsewhere turns accented letters into spaces,
    which is exactly how Luján fails to match LUJAN."""
    name = unicodedata.normalize("NFKD", name or "")
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower().strip()
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v|hon|sen|mr|ms|mrs|dr)\b\.?", "", name)
    name = re.sub(r"[^a-z\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def parse_date(s):
    """MM/DD/YYYY (the XML's only format) and friends -> YYYY-MM-DD, '' on
    failure — a raw unparsed string must never reach stored date fields."""
    if not s or not s.strip():
        return ""
    s = s.strip()
    for fmt in ["%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"]:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def trip_keys(t):
    """Dedupe keys: the DocURL is globally unique, but amended filings get a
    NEW URL for the SAME trip — so also collapse on (traveler, date span)."""
    keys = []
    if t.get("source_doc"):
        keys.append(("doc", t["source_doc"]))
    if t.get("departure_date") and t.get("return_date"):
        keys.append(("span", normalize_name(t.get("traveler", "")),
                     t["departure_date"], t["return_date"]))
    return keys


# ---------------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------------

def download_xml():
    """Download the bulk zip, return giftrule.xml bytes. Fail loud."""
    for attempt in range(3):
        try:
            r = requests.get(ZIP_URL, headers=HEADERS, timeout=60)
            if r.status_code == 200 and r.content[:2] == b"PK":
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    names = [n for n in z.namelist() if n.endswith(".xml")]
                    if not names:
                        print("[FATAL] zip contains no XML file")
                        sys.exit(1)
                    return z.read(names[0])
            print(f"  attempt {attempt + 1}: HTTP {r.status_code}, "
                  f"{len(r.content)} bytes (not a zip)")
        except requests.RequestException as e:
            print(f"  attempt {attempt + 1}: {e}")
        time.sleep(5 * (attempt + 1))
    print("[FATAL] Could not download giftruledata.zip — site down or moved.")
    sys.exit(1)


def parse_member_docs(xml_bytes):
    """All 'Member Reimbursed Travel' documents from the bulk XML."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        print(f"[FATAL] giftrule.xml failed to parse: {e}")
        sys.exit(1)

    filers = root.findall("dbo.filer")
    if not filers:
        print("[FATAL] XML has zero dbo.filer nodes — format changed?")
        sys.exit(1)

    docs = []
    for filer in filers:
        first = (filer.get("FirstName") or "").strip()
        last = (filer.get("LastName") or "").strip()
        for office in filer.findall("dbo.Office"):
            for doc in office.findall("dbo.Document"):
                for rep in doc.findall("dbo.Reports"):
                    title = (rep.get("ReportTitle") or "").strip()
                    url = (rep.get("DocURL") or "").strip()
                    if title != MEMBER_REPORT_TITLE or not url:
                        continue
                    docs.append({
                        "first": first,
                        "last": last,
                        "departure": parse_date(doc.get("BeginTravelDate")),
                        "return": parse_date(doc.get("EndTravelDate")),
                        "received": parse_date(doc.get("DateReceived")),
                        "year": (doc.get("ReportingYear") or "").strip(),
                        "url": url,
                    })
    print(f"Bulk XML: {len(filers)} filers, "
          f"{len(docs)} member travel documents")
    return docs


# ---------------------------------------------------------------------------
# Member matching
# ---------------------------------------------------------------------------

def match_senator(first, last, senators):
    """Match a filer to exactly ONE sitting senator, else None (former
    senators are expected in the 4-year window — never blind-attribute).
    Surname matches the TAIL of the member name so multi-word surnames
    (VAN HOLLEN vs 'Chris Van Hollen') work."""
    nfirst, nlast = normalize_name(first), normalize_name(last)
    if not nlast:
        return None
    cands = [s for s in senators
             if normalize_name(s.get("name", "")).endswith(" " + nlast)
             or normalize_name(s.get("name", "")) == nlast]
    if len(cands) == 1:
        return cands[0]
    if len(cands) > 1 and nfirst:
        ffirst = nfirst.split()[0]
        narrowed = [s for s in cands
                    if normalize_name(s.get("name", "")).startswith(ffirst)]
        if len(narrowed) == 1:
            return narrowed[0]
    return None


def make_trip(doc, senator):
    """House-compatible travel record; destination/sponsor/cost live only in
    the scanned PDF, so they stay empty and source_doc carries the substance."""
    return {
        "traveler": senator["name"],
        "filer_type": "member",
        "destination_country": "",
        "departure_date": doc["departure"],
        "return_date": doc["return"],
        "sponsor": "",
        "total_cost": "",
        "currency": "USD",
        "doc_id": os.path.splitext(os.path.basename(doc["url"]))[0],
        "source_doc": doc["url"],
        "date_received": doc["received"],
        "reporting_year": doc["year"],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("SENATE GIFT-RULE TRAVEL FETCHER")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    members = load_json_strict(MEMBERS_PATH, [])
    if not members:
        print("[FATAL] data/members.json missing or empty")
        sys.exit(1)
    senators = [m for m in members
                if (m.get("chamber") or "").lower() == "senate"]
    print(f"Sitting senators in members.json: {len(senators)}")

    docs = parse_member_docs(download_xml())
    if not docs:
        print("[FATAL] Zero 'Member Reimbursed Travel' documents — the "
              "report title or XML contract changed. Failing loud.")
        sys.exit(1)

    matched = {}     # bioguide id -> [trip dicts]
    unmatched = []
    for doc in docs:
        senator = match_senator(doc["first"], doc["last"], senators)
        if senator is None:
            unmatched.append(f"{doc['last']}, {doc['first']}")
            continue
        matched.setdefault(senator["id"], []).append(make_trip(doc, senator))

    if unmatched:
        uniq = sorted(set(unmatched))
        print(f"\nUnmatched filers ({len(uniq)} — expected: former "
              f"senators in the 4-year window): {'; '.join(uniq[:12])}"
              f"{' ...' if len(uniq) > 12 else ''}")

    if not matched:
        print("[FATAL] Zero member documents matched a sitting senator — "
              "matcher or data contract broke. Failing loud.")
        sys.exit(1)

    if DRY_RUN:
        print(f"\n[DRY RUN] Would update {len(matched)} senators. Samples:")
        for bid, trips in list(matched.items())[:5]:
            print(f"  {bid}: {len(trips)} trips")
            for t in trips[:2]:
                print(f"      {json.dumps(t, ensure_ascii=False)}")
        return

    # Save (safe merge + dedupe), mirroring fetch_travel_pdf.py
    print("\n--- Saving results ---")
    members_by_id = {m["id"]: m for m in members}
    trips_added = 0
    senators_updated = 0

    for bid, trips in matched.items():
        detail_path = os.path.join(DETAILS_DIR, f"{bid}.json")
        detail = load_json_strict(detail_path, {})

        existing = detail.get("travel", []) or []
        seen = set()
        for t in existing:
            seen.update(trip_keys(t))

        added = []
        for t in trips:
            keys = trip_keys(t)
            if keys and any(k in seen for k in keys):
                continue
            added.append(t)
            seen.update(keys)

        if not added:
            continue

        all_travel = existing + added
        detail["travel"] = all_travel
        detail["travel_updated"] = datetime.now(timezone.utc).isoformat()
        detail["travel_count"] = len(all_travel)
        save_json(detail_path, detail)

        if bid in members_by_id:
            members_by_id[bid]["travel_count"] = len(all_travel)

        trips_added += len(added)
        senators_updated += 1
        name = members_by_id.get(bid, {}).get("name", bid)
        print(f"  {name}: +{len(added)} trips ({len(all_travel)} total)")

    if senators_updated:
        save_json(MEMBERS_PATH, members)

    print("=" * 60)
    print("SENATE TRAVEL — COMPLETE")
    print(f"Member documents:   {len(docs)}")
    print(f"Senators matched:   {len(matched)}")
    print(f"Senators updated:   {senators_updated}")
    print(f"Trips added:        {trips_added}")
    print(f"Finished: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
