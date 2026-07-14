"""
CongressWatch — House Foreign Travel Fetcher
==============================================
Pulls House foreign travel (CODEL) data from the House Clerk's
public disclosure pages.

Sources:
  - https://disclosures-clerk.house.gov/ForeignTravel
  - https://clerk.house.gov/public_disc/travel/

The House Clerk publishes annual foreign travel reports as XML files.
This script downloads the XML index, parses individual trip reports,
and matches them to House members in members.json.

Senate travel: Senate disclosures are at
  https://www.senate.gov/legislative/travel_disclosures.htm
  but are published only as PDFs — not yet parseable automatically.

DEAD CODE — no workflow invokes this. Superseded by fetch_travel_pdf.py.
Kept for reference only; it fully OVERWRITES travel[] instead of safe-merging.
"""

import os
import re
import json
import requests
from datetime import datetime

MEMBERS_FILE = "data/members.json"
DETAILS_DIR = "data/details"

# House Clerk travel XML base
TRAVEL_BASE = "https://clerk.house.gov/public_disc/travel"

HEADERS = {
    "User-Agent": "CongressWatch/1.0 (public-interest-research)"
}

os.makedirs(DETAILS_DIR, exist_ok=True)


# ─── Helpers ────────────────────────────────────────────────

def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def normalize_name(name):
    name = name.lower().strip()
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v|hon|rep|mr|ms|mrs)\b", "", name)
    name = re.sub(r"[^a-z\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


# ─── XML Parsing (minimal, no lxml dependency) ─────────────

def extract_xml_tag(text, tag):
    """Extract content of a simple XML tag. Returns list of matches."""
    pattern = f"<{tag}[^>]*>(.*?)</{tag}>"
    return re.findall(pattern, text, re.DOTALL | re.IGNORECASE)


def parse_travel_xml(xml_text):
    """Parse a House Clerk travel XML report into trip records."""
    trips = []
    # Each trip is typically in a <Trip> or <Record> element
    # Try common patterns
    trip_blocks = extract_xml_tag(xml_text, "Trip")
    if not trip_blocks:
        trip_blocks = extract_xml_tag(xml_text, "Record")
    if not trip_blocks:
        trip_blocks = extract_xml_tag(xml_text, "Row")

    for block in trip_blocks:
        trip = {}
        # Try to extract common fields
        for field, keys in [
            ("member_name", ["MemberName", "Name", "Traveler", "FilingMember"]),
            ("destination_country", ["Destination", "Country", "Location"]),
            ("departure_date", ["DepartureDate", "StartDate", "DateOfDeparture"]),
            ("return_date", ["ReturnDate", "EndDate", "DateOfReturn"]),
            ("sponsor", ["Sponsor", "Committee", "Delegation", "GroupName"]),
            ("total_cost", ["TotalCost", "Cost", "Amount", "TotalExpense"]),
        ]:
            for key in keys:
                vals = extract_xml_tag(block, key)
                if vals and vals[0].strip():
                    trip[field] = vals[0].strip()
                    break
        if trip.get("member_name") or trip.get("destination_country"):
            trips.append(trip)

    return trips


# ─── Main ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("CongressWatch — House Foreign Travel Fetcher")
    print("=" * 60)

    members = load_json(MEMBERS_FILE, [])
    if not members:
        print("ERROR: data/members.json not found or empty")
        exit(1)

    house_members = [m for m in members if (m.get("chamber", "") or "").lower() == "house"]
    print(f"Loaded {len(members)} members, {len(house_members)} House members")

    # Build a lookup for House members by normalized last name
    member_by_last = {}
    for m in house_members:
        bid = m.get("id") or m.get("bioguide_id", "")
        name = m.get("name", "")
        parts = name.strip().split()
        if parts and bid:
            last = normalize_name(parts[-1])
            if last not in member_by_last:
                member_by_last[last] = []
            member_by_last[last].append((bid, name, m))

    # Try to fetch XML travel reports from the House Clerk
    # The clerk publishes annual reports; try recent years
    current_year = datetime.now().year
    all_trips = []
    years_tried = 0

    for year in range(current_year, current_year - 4, -1):
        # Try common URL patterns for House Clerk travel XML
        urls_to_try = [
            f"{TRAVEL_BASE}/{year}ForeignTravel.xml",
            f"{TRAVEL_BASE}/{year}_ForeignTravel.xml",
            f"{TRAVEL_BASE}/ForeignTravel{year}.xml",
            f"{TRAVEL_BASE}/{year}.xml",
        ]
        for url in urls_to_try:
            try:
                print(f"  Trying {url}...")
                r = requests.get(url, headers=HEADERS, timeout=15)
                if r.status_code == 200 and "<" in r.text[:100]:
                    trips = parse_travel_xml(r.text)
                    if trips:
                        print(f"    Found {len(trips)} trips for {year}")
                        all_trips.extend(trips)
                        years_tried += 1
                        break
                    else:
                        print(f"    XML fetched but no parseable trips")
            except Exception as e:
                continue

    # Also try the main index page for links
    if not all_trips:
        print(f"\n  No XML reports found. Trying index page...")
        try:
            r = requests.get(f"{TRAVEL_BASE}/", headers=HEADERS, timeout=15)
            if r.status_code == 200:
                # Find links to XML files
                xml_links = re.findall(r'href="([^"]*\.xml)"', r.text, re.IGNORECASE)
                for link in xml_links[:5]:  # Try up to 5
                    full_url = link if link.startswith("http") else f"{TRAVEL_BASE}/{link}"
                    try:
                        print(f"  Trying discovered link: {full_url}")
                        xr = requests.get(full_url, headers=HEADERS, timeout=15)
                        if xr.status_code == 200:
                            trips = parse_travel_xml(xr.text)
                            if trips:
                                print(f"    Found {len(trips)} trips")
                                all_trips.extend(trips)
                    except Exception:
                        continue
        except Exception as e:
            print(f"  Index page error: {e}")

    # Also try the Gift Travel Filings page
    if not all_trips:
        print(f"\n  Trying Gift Travel Filings page...")
        try:
            r = requests.get(
                "https://disclosures-clerk.house.gov/GiftTravelFilings",
                headers=HEADERS, timeout=15
            )
            if r.status_code == 200:
                xml_links = re.findall(r'href="([^"]*\.xml)"', r.text, re.IGNORECASE)
                for link in xml_links[:5]:
                    full_url = link if link.startswith("http") else f"https://disclosures-clerk.house.gov{link}"
                    try:
                        print(f"  Trying gift travel link: {full_url}")
                        xr = requests.get(full_url, headers=HEADERS, timeout=15)
                        if xr.status_code == 200:
                            trips = parse_travel_xml(xr.text)
                            if trips:
                                print(f"    Found {len(trips)} trips")
                                all_trips.extend(trips)
                    except Exception:
                        continue
        except Exception as e:
            print(f"  Gift travel page error: {e}")

    print(f"\nTotal trips parsed: {len(all_trips)}")

    # Match trips to members
    matched_members = 0
    total_matched_trips = 0

    if all_trips:
        # Group trips by member name
        trips_by_member = {}
        for trip in all_trips:
            mname = trip.get("member_name", "")
            if not mname:
                continue
            parts = normalize_name(mname).split()
            last = parts[-1] if parts else ""
            if last not in trips_by_member:
                trips_by_member[last] = []
            trips_by_member[last].append(trip)

        # Match to House members
        for last, trip_list in trips_by_member.items():
            candidates = member_by_last.get(last, [])
            if not candidates:
                continue

            # Use first match (could improve with first name matching)
            bid, name, m_entry = candidates[0]

            # Clean up trip records for storage
            clean_trips = []
            for t in trip_list:
                clean_trips.append({
                    "destination_country": t.get("destination_country", ""),
                    "departure_date": t.get("departure_date", ""),
                    "return_date": t.get("return_date", ""),
                    "sponsor": t.get("sponsor", ""),
                    "total_cost": t.get("total_cost", ""),
                    "currency": "USD"
                })

            matched_members += 1
            total_matched_trips += len(clean_trips)

            # Safe merge into detail file
            detail = load_json(os.path.join(DETAILS_DIR, f"{bid}.json"), {})
            detail["travel"] = clean_trips
            detail["travel_updated"] = datetime.now().isoformat()
            detail["travel_count"] = len(clean_trips)
            save_json(os.path.join(DETAILS_DIR, f"{bid}.json"), detail)

            # Update members.json entry
            m_entry["travel_count"] = len(clean_trips)

            print(f"  {name}: {len(clean_trips)} trips")

    # Save updated members.json
    save_json(MEMBERS_FILE, members)

    # Summary
    print(f"\n{'=' * 60}")
    print(f"HOUSE TRAVEL FETCH COMPLETE")
    print(f"{'=' * 60}")
    print(f"Trips parsed:       {len(all_trips)}")
    print(f"Members matched:    {matched_members}/{len(house_members)}")
    print(f"Trips matched:      {total_matched_trips}")
    if not all_trips:
        print(f"\nNOTE: No machine-readable travel data found at House Clerk.")
        print(f"  The House Clerk may publish travel reports as PDFs only.")
        print(f"  TODO: Add PDF parsing via pypdf for travel reports.")
        print(f"  Manual source: https://clerk.house.gov/public_disc/travel/")
    print(f"{'=' * 60}")
