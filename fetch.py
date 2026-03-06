import os
import json
import time
import requests
from datetime import datetime

CONGRESS_KEY = os.environ.get(“CONGRESS_API_KEY”, “”)
BASE = “https://api.congress.gov/v3”
HEADERS = {“User-Agent”: “CongressWatch/1.0”}

os.makedirs(“data”, exist_ok=True)

def sleep(s=0.5):
time.sleep(s)

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

def normalize(m, chamber):
bid = m.get(“bioguideId”, “”)
return {
“id”: bid,
“name”: fix_name(m.get(“name”, “”)),
“party”: fix_party(m.get(“partyName”, “”)),
“state”: m.get(“state”, “”),
“district”: str(m.get(“district”, “”)),
“chamber”: chamber,
“photo_url”: photo_url(bid),
“term_start”: get_term_start(m),
“score”: 0,
“flags”: [],
“updated”: datetime.now().isoformat(),
}

def fetch_all(chamber):
params = {
“api_key”: CONGRESS_KEY,
“limit”: 250,
“currentMember”: “true”,
“chamber”: chamber,
“offset”: 0,
}
results = []
while True:
try:
sleep(0.5)
r = requests.get(BASE + “/member”, params=params, headers=HEADERS, timeout=30)
r.raise_for_status()
batch = r.json().get(“members”, [])
if not batch:
break
results.extend(batch)
print(chamber + “: “ + str(len(results)) + “ fetched”)
if len(batch) < 250:
break
params[“offset”] += 250
except Exception as e:
print(“Error: “ + str(e))
break
return results

print(“Fetching Senate…”)
senate = fetch_all(“Senate”)
print(“Senate done: “ + str(len(senate)))

print(“Fetching House…”)
house = fetch_all(“House”)
print(“House done: “ + str(len(house)))

seen = {}
for m in senate:
bid = m.get(“bioguideId”, “”)
if bid:
seen[bid] = normalize(m, “Senate”)

for m in house:
bid = m.get(“bioguideId”, “”)
if bid and bid not in seen:
seen[bid] = normalize(m, “House”)

all_members = list(seen.values())
s = sum(1 for m in all_members if m[“chamber”] == “Senate”)
h = sum(1 for m in all_members if m[“chamber”] == “House”)

print(“Total: “ + str(len(all_members)) + “ | Senate: “ + str(s) + “ | House: “ + str(h))

with open(“data/members.json”, “w”) as f:
json.dump(all_members, f, indent=2)

print(“Done. Saved data/members.json”)
