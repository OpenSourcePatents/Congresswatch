import os
import json
import time
import requests
from datetime import datetime

CONGRESS_KEY = os.environ.get(“CONGRESS_API_KEY”, “”)
BASE = “https://api.congress.gov/v3”
HEADERS = {“User-Agent”: “CongressWatch/1.0”}

os.makedirs(“data”, exist_ok=True)

def photo_url(bid):
if not bid:
return “”
return “https://bioguide.congress.gov/bioguide/photo/” + bid[0].upper() + “/” + bid + “.jpg”

def fix_party(p):
p = (p or “”).strip().lower()
if “democrat” in p:
return “Democratic”
if “republican” in p:
return “Republican”
if “independent” in p:
return “Independent”
return p or “Unknown”

def fix_name(raw):
if “,” in raw:
parts = raw.split(”,”, 1)
return parts[1].strip() + “ “ + parts[0].strip()
return raw

def get_term_start(m):
terms = m.get(“terms”, {})
if isinstance(terms, dict):
items = terms.get(“item”, [])
if isinstance(items, list) and len(items) > 0:
yr = items[0].get(“startYear”, “”) or items[0].get(“start”, “”)
if yr:
return str(yr) + “-01-01” if len(str(yr)) == 4 else str(yr)
if isinstance(terms, list) and len(terms) > 0:
yr = terms[0].get(“startYear”, “”) or terms[0].get(“start”, “”)
if yr:
return str(yr) + “-01-01” if len(str(yr)) == 4 else str(yr)
yr = m.get(“startYear”, “”) or m.get(“termStart”, “”)
if yr:
return str(yr) + “-01-01” if len(str(yr)) == 4 else str(yr)
return “2010-01-01”

TERRITORIES = {
“District of Columbia”, “Puerto Rico”, “Virgin Islands”,
“Guam”, “American Soma”, “Northern Mariana Islands”
}

DEBUG_IDS = {“S000033”, “S000148”, “G000386”, “D000563”, “W000779”}

def infer_chamber(m):
state = m.get(“state”, “”)
raw_district = m.get(“district”, None)

```
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
```

def normalize(m):
bid = m.get(“bioguideId”, “”)
chamber, district = infer_chamber(m)
return {
“id”: bid,
“name”: fix_name(m.get(“name”, “”)),
“party”: fix_party(m.get(“partyName”, “”)),
“state”: m.get(“state”, “”),
“district”: district,
“chamber”: chamber,
“photo_url”: photo_url(bid),
“term_start”: get_term_start(m),
“score”: 0,
“flags”: [],
“updated”: datetime.now().isoformat(),
}

def fetch_all():
limit = 200
params = {
“api_key”: CONGRESS_KEY,
“limit”: limit,
“currentMember”: “true”,
“offset”: 0,
}
results = []
while True:
try:
time.sleep(0.5)
r = requests.get(BASE + “/member”, params=params, headers=HEADERS, timeout=30)
r.raise_for_status()
batch = r.json().get(“members”, [])
if not batch:
break
results.extend(batch)
print(“Fetched: “ + str(len(results)))
if len(batch) < limit:
break
params[“offset”] += limit
except Exception as e:
print(“Error: “ + str(e))
break
return results

print(“Fetching members…”)
raw = fetch_all()
print(“Raw total: “ + str(len(raw)))

# Write raw data for 5 known senators to a debug file in the repo

debug_records = [m for m in raw if m.get(“bioguideId”, “”) in DEBUG_IDS]
with open(“data/debug_raw.json”, “w”) as f:
json.dump(debug_records, f, indent=2)
print(“Wrote data/debug_raw.json with “ + str(len(debug_records)) + “ records”)

seen = {}
for m in raw:
bid = m.get(“bioguideId”, “”)
if bid and bid not in seen:
seen[bid] = normalize(m)

all_members = list(seen.values())
s = sum(1 for m in all_members if m[“chamber”] == “Senate”)
h = sum(1 for m in all_members if m[“chamber”] == “House”)

print(“Total: “ + str(len(all_members)) + “ | Senate: “ + str(s) + “ | House: “ + str(h))

with open(“data/members.json”, “w”) as f:
json.dump(all_members, f, indent=2)

print(“Done. Saved data/members.json”)
