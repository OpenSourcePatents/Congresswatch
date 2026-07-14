import os
import json
import time
import requests
from datetime import datetime

CONGRESS_KEY = os.environ.get("CONGRESS_API_KEY", "")
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
    """Paginate /member with per-page retries.

    Returns (results, complete). complete=False means a page failed after
    all retries — callers must NOT treat the partial list as the roster.
    """
    limit = 200
    params = {
        "api_key": CONGRESS_KEY,
        "limit": limit,
        "currentMember": "true",
        "offset": 0,
    }
    results = []
    max_retries = 4
    while True:
        batch = None
        for attempt in range(max_retries):
            try:
                time.sleep(0.5)
                r = requests.get(BASE + "/member", params=params, headers=HEADERS, timeout=30)
                r.raise_for_status()
                batch = r.json().get("members", [])
                break
            except Exception as e:
                wait = 2 ** attempt
                print(f"Error at offset {params['offset']} "
                      f"(attempt {attempt + 1}/{max_retries}): {e} — retrying in {wait}s")
                time.sleep(wait)
        if batch is None:
            print(f"FAILED: offset {params['offset']} unreachable after {max_retries} attempts")
            return results, False
        if not batch:
            break
        results.extend(batch)
        print("Fetched: " + str(len(results)))
        if len(batch) < limit:
            break
        params["offset"] += limit
    return results, True


BASE_FIELDS = {"name", "party", "state", "district", "chamber", "photo_url", "term_start", "data_updated"}


if __name__ == "__main__":
    print("Fetching members...")
    raw, complete = fetch_all()
    print("Raw total: " + str(len(raw)))

    # A partial fetch must never become the roster: the merge below drops
    # any member absent from `raw`, so members missing only because a page
    # failed would silently vanish from members.json.
    if not complete:
        print("ABORT: pagination incomplete — refusing to rebuild members.json "
              "from a partial member list.")
        exit(1)

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
    except FileNotFoundError:
        pass
    except json.JSONDecodeError as e:
        # A corrupt file must not silently become "no existing data" — that
        # would strip every pipeline-added field (scores, trades, finance)
        print(f"ABORT: data/members.json exists but failed to parse ({e}).")
        exit(1)

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

    # Atomic write: temp file + os.replace so a crash never truncates the file
    tmp_path = "data/members.json.tmp"
    with open(tmp_path, "w") as f:
        json.dump(all_members, f, indent=2)
    os.replace(tmp_path, "data/members.json")

    print("Done. Saved data/members.json")
