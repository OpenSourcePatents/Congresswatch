#!/usr/bin/env python3
"""
fetch_travel_pdf.py — House Gift Travel Disclosure Fetcher
===========================================================
Fetches privately-sponsored (gift) travel filings for House members from
the House Clerk's Gift Travel Filings database.

Source of truth (verified live 2026-07):
  POST https://disclosures-clerk.house.gov/GiftTravelFilings/ViewSearchResult
  with an empty search returns a consolidated HTML table of ALL filings:
  Member Name ("Last, First"), Filer Name, Destination(s), Travel Dates
  (with a machine-sortable data-sort attribute), Sponsor, and a link to the
  underlying disclosure document (gtimages/{MT|ST}/{year}/{doc_id}.pdf).
  MT = the member is the traveler; ST = a staffer in the member's office.
  Server-side filtering via TravelDateFrom (MM/DD/YYYY) is supported.

Why PDFs are no longer parsed:
  The old approach scraped https://clerk.house.gov/public_disc/travel/ for
  PDFs, but that URL now redirects to a generic HTML page with no travel
  PDFs. The disclosure documents themselves are SCANNED images (verified:
  pdfplumber extracts zero field text from live MT/ST filings), so per-trip
  costs are not machine-readable without OCR. All structured fields we need
  come from the HTML index; total_cost is therefore reported as 0.0
  (unknown), never a garbage value. Because no PDFs are downloaded, no PDF
  cache is required.

Attribution rules:
  - Only MT (member-travel) filings are attributed to members. ST (staff)
    filings are counted in stats and skipped.
  - Member matching uses the index's "Last, First" column against House
    members in members.json. Ambiguous or unmatched names are SKIPPED and
    counted — never guessed.

Output contract (unchanged):
  - data/details/{bioguide_id}.json — travel[] appended with dedup
    (safe merge: existing keys in the detail file are never dropped)
  - data/members.json — travel_count per matched member

Run:
  pip install requests beautifulsoup4
  python fetch_travel_pdf.py            # full run (writes data/)
  python fetch_travel_pdf.py --dry-run  # fetch + parse + match, no writes
"""

import json
import os
import re
import sys
import time
import tempfile
import requests
from datetime import datetime, timezone
from urllib.parse import urljoin
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
MEMBERS_PATH = os.path.join(BASE_DIR, "data", "members.json")
DETAILS_DIR  = os.path.join(BASE_DIR, "data", "details")

SITE_BASE   = "https://disclosures-clerk.house.gov/"
SEARCH_URL  = SITE_BASE + "GiftTravelFilings/ViewSearchResult"
USER_AGENT  = ("CongressWatch/1.0 "
               "(public-interest-research; "
               "mailto:project.congress.watch@gmail.com)")

# How many years of filings to request (server-side TravelDateFrom filter)
LOOKBACK_YEARS = 3

DRY_RUN = "--dry-run" in sys.argv


# ---------------------------------------------------------------------------
# JSON helpers (atomic write, strict load)
# ---------------------------------------------------------------------------

def load_json_strict(path, default):
    """Load JSON. Missing or empty file -> default. An EXISTING, NON-EMPTY
    file that fails to parse ABORTS the run — silently returning a default
    here could wipe fields other pipelines wrote into the same file."""
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
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_travel_",
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
    """Lowercase, strip suffixes and non-alpha."""
    name = (name or "").lower().strip()
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v|hon|rep|mr|ms|mrs|dr)\b\.?", "", name)
    name = re.sub(r"[^a-z\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def parse_date(s):
    """Parse common date formats to YYYY-MM-DD. Returns '' on failure —
    a raw unparsed string must never reach the stored date fields."""
    if not s or not s.strip():
        return ""
    s = s.strip()
    for fmt in ["%m/%d/%Y", "%Y/%m/%d", "%m/%d/%y", "%Y-%m-%d",
                "%B %d, %Y", "%b %d, %Y", "%m-%d-%Y"]:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def split_date_range(text):
    """Split '08/15/2024 - 08/16/2024' into (departure, return) raw strings."""
    text = (text or "").strip()
    if not text:
        return "", ""
    parts = re.split(r"\s+[-–]\s+", text)
    if len(parts) >= 2:
        return parts[0].strip(), parts[1].strip()
    return text, ""


# ---------------------------------------------------------------------------
# Fetch & parse the Clerk's consolidated filings table
# ---------------------------------------------------------------------------

def fetch_filings_html(session):
    """POST an empty search (date-bounded) and return the results HTML,
    or None on failure."""
    from_date = f"01/01/{datetime.now().year - LOOKBACK_YEARS}"
    payload = {
        "MemberLastName": "",
        "StaffLastName": "",
        "TravelDateFrom": from_date,
        "TravelDateTo": "",
        "Sponsor": "",
        "Destination": "",
    }
    print(f"[TRAVEL] Querying Gift Travel Filings database "
          f"(travel dates from {from_date})")

    for attempt in range(3):
        if attempt:
            wait = 10 * (2 ** (attempt - 1))
            print(f"[TRAVEL] Retry {attempt + 1}/3 in {wait}s...")
            time.sleep(wait)
        try:
            resp = session.post(SEARCH_URL, data=payload, timeout=120)
        except Exception as e:
            print(f"[TRAVEL] Request error: {e}")
            continue
        if resp.status_code == 200 and "<table" in resp.text:
            print(f"[TRAVEL] Got results page ({len(resp.text):,} bytes)")
            return resp.text
        print(f"[TRAVEL] HTTP {resp.status_code}, "
              f"body starts: {resp.text[:120]!r}")
    return None


def parse_filings(html):
    """Parse the results table into filing dicts."""
    soup = BeautifulSoup(html, "html.parser")
    filings = []

    for row in soup.find_all("tr"):
        tds = row.find_all("td")
        if len(tds) < 5:
            continue

        member_td, filer_td, dest_td, dates_td, sponsor_td = tds[:5]

        member_name = member_td.get_text(" ", strip=True)
        if not member_name:
            continue

        link = member_td.find("a", href=True)
        doc_url, doc_id, filer_type = "", "", ""
        if link:
            href = link["href"].strip()
            doc_url = urljoin(SITE_BASE, href)
            doc_id = os.path.splitext(os.path.basename(href))[0]
            path_upper = "/" + href.upper()
            if "/MT/" in path_upper:
                filer_type = "member"
            elif "/ST/" in path_upper:
                filer_type = "staff"

        filer_name = filer_td.get_text(" ", strip=True)
        if not filer_type:
            # No MT/ST hint in the URL: infer from filer vs member name.
            m_norm = normalize_name(member_name).split()
            f_norm = normalize_name(filer_name).split()
            filer_type = ("member" if m_norm and f_norm and
                          set(m_norm) == set(f_norm) else "staff")

        destination = dest_td.get_text("; ", strip=True)
        destination = re.sub(r"\s*;\s*", "; ", destination).strip("; ").strip()

        dep_raw, ret_raw = split_date_range(dates_td.get_text(" ", strip=True))
        departure = parse_date(dates_td.get("data-sort", "")) or parse_date(dep_raw)
        ret = parse_date(ret_raw)

        filings.append({
            "member_name": member_name,      # "Last, First"
            "traveler": filer_name,
            "filer_type": filer_type,
            "destination": destination,
            "departure_date": departure,
            "departure_date_raw": dep_raw,
            "return_date": ret,
            "return_date_raw": ret_raw,
            "sponsor": sponsor_td.get_text(" ", strip=True),
            "doc_id": doc_id,
            "doc_url": doc_url,
        })

    return filings


# ---------------------------------------------------------------------------
# Member matching — no blind fallbacks
# ---------------------------------------------------------------------------

def build_member_lookup(members):
    """Index House members by normalized last-name token."""
    by_last = {}
    house = []
    for m in members:
        if (m.get("chamber", "") or "").lower() != "house":
            continue
        if not m.get("id") or not m.get("name"):
            continue
        norm = normalize_name(m["name"])
        if not norm:
            continue
        house.append((norm, m))
        by_last.setdefault(norm.split()[-1], []).append((norm, m))
    return by_last, house


def match_member(member_name, by_last, house):
    """Match a 'Last, First' string to exactly one House member.

    Returns (bioguide_id, 'matched') on a unique, first-name-confirmed
    match; (None, 'unmatched') or (None, 'ambiguous') otherwise. Never
    falls back to an arbitrary candidate."""
    if "," in member_name:
        last_part, first_part = member_name.split(",", 1)
    else:
        last_part, first_part = member_name, ""

    norm_last = normalize_name(last_part)
    norm_first = normalize_name(first_part)
    if not norm_last:
        return None, "unmatched"

    last_token = norm_last.split()[-1]
    candidates = list(by_last.get(last_token, []))
    if len(norm_last.split()) > 1:
        # Multi-word last name ("Van Duyne"): also try full-suffix match.
        for norm, m in house:
            if norm.endswith(norm_last) and (norm, m) not in candidates:
                candidates.append((norm, m))
    if not candidates:
        return None, "unmatched"

    first_token = norm_first.split()[0] if norm_first else ""
    if not first_token:
        # No first name to confirm with: accept only a unique candidate.
        if len(candidates) == 1:
            return candidates[0][1]["id"], "matched"
        return None, "ambiguous"

    exact = [c for c in candidates if c[0].split()[0] == first_token]
    if len(exact) == 1:
        return exact[0][1]["id"], "matched"
    if len(exact) > 1:
        return None, "ambiguous"

    # Prefix tolerance ("Dan" vs "Daniel"), both directions, min 3 chars,
    # and only when it identifies a UNIQUE candidate.
    prefix = []
    for c in candidates:
        cand_first = c[0].split()[0]
        if (len(first_token) >= 3 and len(cand_first) >= 3 and
                (cand_first.startswith(first_token) or
                 first_token.startswith(cand_first))):
            prefix.append(c)
    if len(prefix) == 1:
        return prefix[0][1]["id"], "matched"
    if len(prefix) > 1:
        return None, "ambiguous"

    return None, "unmatched"


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------

def trip_keys(t):
    """Identity keys for a trip. doc_id is the Clerk's stable per-filing id.
    The composite fallback includes traveler, sponsor, BOTH dates and the
    destination — and is only used when a departure date (parsed or raw)
    exists, so unparseable-date trips are never collapsed together."""
    keys = []
    doc = (t.get("doc_id") or "").strip()
    if doc:
        keys.append(("doc", doc))
    dep = t.get("departure_date") or t.get("departure_date_raw") or ""
    ret = t.get("return_date") or t.get("return_date_raw") or ""
    if dep:
        keys.append((
            "composite",
            normalize_name(t.get("traveler", "")),
            (t.get("sponsor") or "").strip().lower(),
            dep,
            ret,
            (t.get("destination_country") or "").strip().lower(),
        ))
    return keys


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("CongressWatch — House Gift Travel Disclosure Fetcher")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}"
          + ("  [DRY RUN — no writes]" if DRY_RUN else ""))
    print("=" * 60)

    members = load_json_strict(MEMBERS_PATH, [])
    if not members:
        print("[FATAL] data/members.json not found or empty.")
        sys.exit(1)

    by_last, house = build_member_lookup(members)
    print(f"Loaded {len(house)} House members from {len(members)} total")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    html = fetch_filings_html(session)
    if html is None:
        print("[FATAL] Could not fetch the Gift Travel Filings search "
              "results. No data was written.")
        sys.exit(1)

    filings = parse_filings(html)
    print(f"[TRAVEL] Parsed {len(filings)} filing rows")
    if not filings:
        print("[FATAL] Results page fetched but zero filings parsed — the "
              "page format may have changed. No data was written.")
        sys.exit(1)

    stats = {
        "rows_parsed": len(filings),
        "member_filings": 0,
        "staff_filings_skipped": 0,
        "matched": 0,
        "unmatched_skipped": 0,
        "ambiguous_skipped": 0,
        "members_updated": 0,
        "trips_added": 0,
    }

    # bioguide_id -> [trip dicts]
    all_trips = {}
    skipped_names = {}

    for f in filings:
        if f["filer_type"] != "member":
            stats["staff_filings_skipped"] += 1
            continue
        stats["member_filings"] += 1

        bid, status = match_member(f["member_name"], by_last, house)
        if status != "matched":
            stats[status + "_skipped"] += 1
            skipped_names[f["member_name"]] = status
            continue
        stats["matched"] += 1

        trip = {
            "destination_country": f["destination"],
            "departure_date": f["departure_date"],
            "return_date": f["return_date"],
            "sponsor": f["sponsor"],
            "traveler": f["traveler"],
            "filer_type": "member",
            # Costs are only on scanned (image) PDFs — not machine-readable.
            "total_cost": 0.0,
            "currency": "USD",
            "doc_id": f["doc_id"],
            "source_doc": f["doc_url"],
        }
        # Keep raw date strings only when parsing failed, for auditability.
        if not f["departure_date"] and f["departure_date_raw"]:
            trip["departure_date_raw"] = f["departure_date_raw"]
        if not f["return_date"] and f["return_date_raw"]:
            trip["return_date_raw"] = f["return_date_raw"]

        all_trips.setdefault(bid, []).append(trip)

    if skipped_names:
        print(f"\n[TRAVEL] Skipped filers (no unique member match): "
              f"{len(skipped_names)}")
        for n, why in sorted(skipped_names.items())[:20]:
            print(f"    {why}: {n}")

    if not all_trips:
        print("[FATAL] Zero member trips matched — nothing to write. "
              "Exiting without touching data files.")
        sys.exit(1)

    if DRY_RUN:
        print(f"\n[DRY RUN] Would update {len(all_trips)} members. Samples:")
        members_by_id = {m["id"]: m for m in members}
        for bid, trips in list(all_trips.items())[:5]:
            name = members_by_id.get(bid, {}).get("name", "?")
            print(f"  {bid} ({name}): {len(trips)} trips")
            for t in trips[:2]:
                print(f"      {json.dumps(t, ensure_ascii=False)}")
        _print_summary(stats)
        return

    # Save results (safe merge + dedup)
    print("\n--- Saving results ---")
    members_by_id = {m["id"]: m for m in members}

    for bid, trips in all_trips.items():
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

        all_travel = existing + added
        detail["travel"] = all_travel
        detail["travel_updated"] = datetime.now(timezone.utc).isoformat()
        detail["travel_count"] = len(all_travel)
        save_json(detail_path, detail)

        if bid in members_by_id:
            members_by_id[bid]["travel_count"] = len(all_travel)

        stats["trips_added"] += len(added)
        stats["members_updated"] += 1

        name = members_by_id.get(bid, {}).get("name", bid)
        print(f"  {name}: +{len(added)} trips ({len(all_travel)} total)")

    save_json(MEMBERS_PATH, members)
    _print_summary(stats)


def _print_summary(stats):
    print("\n" + "=" * 60)
    print("HOUSE GIFT TRAVEL FETCH — COMPLETE"
          + (" (DRY RUN)" if DRY_RUN else ""))
    print("=" * 60)
    print(f"Filing rows parsed:      {stats['rows_parsed']}")
    print(f"Member filings:          {stats['member_filings']}")
    print(f"Staff filings skipped:   {stats['staff_filings_skipped']}")
    print(f"Matched to members:      {stats['matched']}")
    print(f"Unmatched (skipped):     {stats['unmatched_skipped']}")
    print(f"Ambiguous (skipped):     {stats['ambiguous_skipped']}")
    print(f"Members updated:         {stats['members_updated']}")
    print(f"New trips added:         {stats['trips_added']}")
    print(f"Finished: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
