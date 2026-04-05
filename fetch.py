import os
import json
import time
import requests
from datetime import datetime

CONGRESS_KEY = os.environ.get("CONGRESS_API_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
BASE = "https://api.congress.gov/v3"
HEADERS = {"User-Agent": "CongressWatch/1.0"}

os.makedirs("data", exist_ok=True)


def photo_url(bid):
    if not bid:
        return ""
    return "https://bioguide.congress.gov/bioguide/photo/" + bid[0].upper() + "/" + bid + ".jpg"


def fix_party(p):
    p = (p or "").strip().lower()
    if "democrat" in p:
        return "Democratic"
    if "republican" in p:
        return "Republican"
    if "independent" in p:
        return "Independent"
    return p or "Unknown"


def fix_name(raw):
    if "," in raw:
        parts = raw.split(",", 1)
        return parts[1].strip() + " " + parts[0].strip()
    return raw


def get_term_start(m):
    terms = m.get("terms", {})
    if isinstance(terms, dict):
        items = terms.get("item", [])
        if isinstance(items, list) and len(items) > 0:
            yr = items[0].get("startYear", "") or items[0].get("start", "")
            if yr:
                return str(yr) + "-01-01" if len(str(yr)) == 4 else str(yr)
    if isinstance(terms, list) and len(terms) > 0:
        yr = terms[0].get("startYear", "") or terms[0].get("start", "")
        if yr:
            return str(yr) + "-01-01" if len(str(yr)) == 4 else str(yr)
    yr = m.get("startYear", "") or m.get("termStart", "")
    if yr:
        return str(yr) + "-01-01" if len(str(yr)) == 4 else str(yr)
    return "2010-01-01"


TERRITORIES = {
    "District of Columbia", "Puerto Rico", "Virgin Islands",
    "Guam", "American Samoa", "Northern Mariana Islands"
}


def infer_chamber(m):
    state = m.get("state", "")
    raw_district = m.get("district", None)

    if raw_district not in (None, "", "None", 0, "0"):
        try:
            d = int(str(raw_district))
            if d > 0:
                return "House", str(d)
        except (ValueError, TypeError):
            pass

    if state in TERRITORIES:
        return "House", ""

    terms = m.get("terms", {})
    items = []
    if isinstance(terms, dict):
        items = terms.get("item", []) or []
    elif isinstance(terms, list):
        items = terms

    if items:
        most_recent = items[-1]
        ct = (most_recent.get("chamber", "") or "").lower()
        if "senate" in ct:
            return "Senate", ""
        if "house" in ct:
            return "House", str(raw_district or "")

    member_type = (m.get("type", "") or "").lower()
    if "senator" in member_type or "senate" in member_type:
        return "Senate", ""
    if "representative" in member_type or "house" in member_type:
        return "House", str(raw_district or "")

    return "Senate", ""


def normalize(m):
    bid = m.get("bioguideId", "")
    chamber, district = infer_chamber(m)
    return {
        "id": bid,
        "name": fix_name(m.get("name", "")),
        "party": fix_party(m.get("partyName", "")),
        "state": m.get("state", ""),
        "district": district,
        "chamber": chamber,
        "photo_url": photo_url(bid),
        "term_start": get_term_start(m),
        "data_updated": datetime.now().isoformat(),
    }


def fetch_all():
    limit = 200
    params = {
        "api_key": CONGRESS_KEY,
        "limit": limit,
        "currentMember": "true",
        "offset": 0,
    }
    results = []
    while True:
        try:
            time.sleep(0.5)
            r = requests.get(BASE + "/member", params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            batch = r.json().get("members", [])
            if not batch:
                break
            results.extend(batch)
            print("Fetched: " + str(len(results)))
            if len(batch) < limit:
                break
            params["offset"] += limit
        except Exception as e:
            print("Error: " + str(e))
            break
    return results


BASE_FIELDS = {"name", "party", "state", "district", "chamber", "photo_url", "term_start", "data_updated"}


def supabase_upsert_members(members_list):
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Supabase not configured, skipping")
        return

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }

    rows = []
    for m in members_list:
        rows.append({
            "bioguide_id": m.get("id") or m.get("bioguide_id"),
            "name": m.get("name", ""),
            "party": m.get("party", "Unknown"),
            "state": m.get("state", ""),
            "district": m.get("district", ""),
            "chamber": m.get("chamber", "House"),
            "photo_url": m.get("photo_url", ""),
            "term_start": m.get("term_start", "2010-01-01"),
            "congress_updated": datetime.now().isoformat()
        })

    success = 0
    for i in range(0, len(rows), 50):
        chunk = rows[i:i+50]
        try:
            r = requests.post(
                f"{SUPABASE_URL}/rest/v1/members",
                headers=headers,
                json=chunk,
                timeout=30
            )
            if r.status_code in (200, 201):
                success += len(chunk)
            else:
                print(f"Supabase batch {i}: {r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"Supabase batch {i} error: {e}")

    print(f"Supabase: {success}/{len(rows)} members upserted")


if __name__ == "__main__":
    print("Fetching members...")
    raw = fetch_all()
    print("Raw total: " + str(len(raw)))

    seen = {}
    for m in raw:
        bid = m.get("bioguideId", "")
        if bid and bid not in seen:
            seen[bid] = normalize(m)

    # Load existing members.json to preserve finance/pipeline fields
    existing_by_id = {}
    try:
        with open("data/members.json") as f:
            for em in json.load(f):
                eid = em.get("id", "")
                if eid:
                    existing_by_id[eid] = em
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Merge: update only base Congress.gov fields, preserve everything else
    all_members = []
    for bid, new_entry in seen.items():
        if bid in existing_by_id:
            merged = existing_by_id[bid]
            for field in BASE_FIELDS:
                if field in new_entry:
                    merged[field] = new_entry[field]
            # Ensure id is set
            merged["id"] = bid
            all_members.append(merged)
        else:
            all_members.append(new_entry)

    s = sum(1 for m in all_members if m["chamber"] == "Senate")
    h = sum(1 for m in all_members if m["chamber"] == "House")

    print("Total: " + str(len(all_members)) + " | Senate: " + str(s) + " | House: " + str(h))

    # Safety check: never write an empty or suspiciously small member list.
    # Congress has 535+ voting members; anything below 400 means the API
    # fetch failed or returned partial data. Preserve the existing file.
    if len(all_members) < 400:
        print(f"ABORT: Only {len(all_members)} members — refusing to overwrite "
              f"members.json (expected 535+). API may be down or key missing.")
        exit(1)

    with open("data/members.json", "w") as f:
        json.dump(all_members, f, indent=2)

    print("Done. Saved data/members.json")

    # Upsert to Supabase (non-blocking — JSON is already saved)
    try:
        supabase_upsert_members(all_members)
    except Exception as e:
        print(f"Supabase upsert failed (JSON still saved): {e}")
