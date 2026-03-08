“””
CongressWatch — Finance & Trade Data Fetcher (Production v2.5.0)
Pulls: FEC donors, SEC EDGAR signals, GovTrack stats, anomaly scores
SPLIT ARCHITECTURE: Grid data stays in members.json; Deep data moves to details/
“””

import os
import json
import time
import re
import requests
from datetime import datetime

CONGRESS_KEY = os.environ.get(‘CONGRESS_API_KEY’, ‘’)
FEC_KEY = os.environ.get(‘FEC_API_KEY’, ‘DEMO_KEY’)
HEADERS = {
‘User-Agent’: ‘CongressWatch/1.0 (public-interest-research; mailto:project.congress.watch@gmail.com)’,
‘Accept-Encoding’: ‘gzip, deflate’
}

FEC_BASE = ‘https://api.open.fec.gov/v1’
OUTPUT_FILE = ‘data/members.json’
DETAILS_DIR = ‘data/details’

os.makedirs(DETAILS_DIR, exist_ok=True)

# ─── INFRASTRUCTURE CONFIG ───────────────────────────────────────────────────

# ONLY these fields stay in members.json (leaderboard grid).

# Everything else goes to data/details/{bioguideId}.json.

# Add new grid-visible fields here intentionally — don’t let the list grow by accident.

LIGHT_FIELDS = {
‘id’, ‘bioguide_id’, ‘name’, ‘party’, ‘state’, ‘district’, ‘chamber’,
‘photo_url’, ‘term_start’, ‘score’, ‘flags’, ‘corporate_insider_signals’,
‘total_raised_display’, ‘missed_votes_pct’, ‘votes_with_party_pct’,
‘govtrack_id’, ‘data_updated’
}

STATE_MAP = {
‘Alabama’: ‘AL’, ‘Alaska’: ‘AK’, ‘Arizona’: ‘AZ’, ‘Arkansas’: ‘AR’, ‘California’: ‘CA’,
‘Colorado’: ‘CO’, ‘Connecticut’: ‘CT’, ‘Delaware’: ‘DE’, ‘Florida’: ‘FL’, ‘Georgia’: ‘GA’,
‘Hawaii’: ‘HI’, ‘Idaho’: ‘ID’, ‘Illinois’: ‘IL’, ‘Indiana’: ‘IN’, ‘Iowa’: ‘IA’,
‘Kansas’: ‘KS’, ‘Kentucky’: ‘KY’, ‘Louisiana’: ‘LA’, ‘Maine’: ‘ME’, ‘Maryland’: ‘MD’,
‘Massachusetts’: ‘MA’, ‘Michigan’: ‘MI’, ‘Minnesota’: ‘MN’, ‘Mississippi’: ‘MS’, ‘Missouri’: ‘MO’,
‘Montana’: ‘MT’, ‘Nebraska’: ‘NE’, ‘Nevada’: ‘NV’, ‘New Hampshire’: ‘NH’, ‘New Jersey’: ‘NJ’,
‘New Mexico’: ‘NM’, ‘New York’: ‘NY’, ‘North Carolina’: ‘NC’, ‘North Dakota’: ‘ND’, ‘Ohio’: ‘OH’,
‘Oklahoma’: ‘OK’, ‘Oregon’: ‘OR’, ‘Pennsylvania’: ‘PA’, ‘Rhode Island’: ‘RI’, ‘South Carolina’: ‘SC’,
‘South Dakota’: ‘SD’, ‘Tennessee’: ‘TN’, ‘Texas’: ‘TX’, ‘Utah’: ‘UT’, ‘Vermont’: ‘VT’,
‘Virginia’: ‘VA’, ‘Washington’: ‘WA’, ‘West Virginia’: ‘WV’, ‘Wisconsin’: ‘WI’, ‘Wyoming’: ‘WY’,
‘District of Columbia’: ‘DC’
}

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def sleep(s=1.2):
time.sleep(s)

def load_members():
try:
with open(OUTPUT_FILE) as f:
return json.load(f)
except Exception as e:
print(f’Critical Error: Could not load {OUTPUT_FILE}: {e}’)
return []

def load_detail(bid):
“”“Load existing detail file for a member, or return empty dict.”””
detail_path = os.path.join(DETAILS_DIR, f’{bid}.json’)
if os.path.exists(detail_path):
with open(detail_path, ‘r’) as f:
try:
return json.load(f)
except Exception:
return {}
return {}

def save_detail(bid, data):
detail_path = os.path.join(DETAILS_DIR, f’{bid}.json’)
with open(detail_path, ‘w’) as f:
json.dump(data, f, indent=2)

# ─── EDGAR ───────────────────────────────────────────────────────────────────

def normalize_name_for_edgar(name):
variations = []
parts = name.strip().split()
if len(parts) < 2:
return [name]
first, last = parts[0], parts[-1]
middle = parts[1] if len(parts) > 2 else ‘’
variations.append(f’{first} {last}’)
variations.append(f’{last}, {first}’)
variations.append(f’{first[0]}. {last}’)
variations.append(f’{last}, {first[0]}.’)
if middle:
variations.append(f’{first} {middle[0]}. {last}’)
return list(dict.fromkeys(variations))

def fetch_edgar_signals(member_name):
variations = normalize_name_for_edgar(member_name)
max_hits = 0
best_var = ‘’
for name_var in variations:
try:
query = name_var.replace(’ ‘, ‘+’)
url = (
f’https://efts.sec.gov/LATEST/search-index’
f’?q=%22{query}%22&forms=4&dateRange=custom&startdt=2023-01-01’
)
sleep(1.2)
r = requests.get(url, headers=HEADERS, timeout=15)
if r.status_code == 200:
data = r.json()
hits = len(data.get(‘hits’, {}).get(‘hits’, []))
if hits > max_hits:
max_hits = hits
best_var = name_var
except Exception:
continue
if max_hits > 0:
print(f’    EDGAR Hit: {max_hits} signals via '{best_var}'’)
return max_hits

# ─── FEC ─────────────────────────────────────────────────────────────────────

def fetch_fec_candidate(name, state_full, office):
clean_name = re.sub(r’\s+[A-Z].?\s+’, ’ ‘, name).strip()
parts = clean_name.split()
fec_name = f”{parts[-1]}, {’ ‘.join(parts[:-1])}” if len(parts) >= 2 else clean_name
state_abbr = STATE_MAP.get(state_full, state_full)
params = {
‘api_key’: FEC_KEY,
‘q’: fec_name,
‘state’: state_abbr,
‘office’: office,
‘per_page’: 3
}
try:
sleep(0.5)
r = requests.get(f’{FEC_BASE}/candidates/search/’, params=params, headers=HEADERS, timeout=20)
r.raise_for_status()
results = r.json().get(‘results’, [])
return results[0] if results else {}
except Exception:
return {}

def fetch_fec_totals(candidate_id):
params = {
‘api_key’: FEC_KEY,
‘candidate_id’: candidate_id,
‘cycle’: 2026,
‘per_page’: 1
}
try:
sleep(0.5)
r = requests.get(f’{FEC_BASE}/candidates/totals/’, params=params, headers=HEADERS, timeout=20)
r.raise_for_status()
results = r.json().get(‘results’, [])
if results:
res = results[0]
return {
‘total_raised’: res.get(‘receipts’, 0),
‘total_spent’: res.get(‘disbursements’, 0),
‘pac_contributions’: res.get(‘contributions_from_other_committees’, 0),
‘individual_contributions’: res.get(‘individual_itemized_contributions’, 0),
‘cash_on_hand’: res.get(‘cash_on_hand_end_period’, 0),
}
except Exception:
return {}
return {}

# ─── SCORING ─────────────────────────────────────────────────────────────────

def compute_score(m):
score = 0
total = m.get(‘total_raised’, 0) or 1
if total > 20_000_000: score += 20
elif total > 10_000_000: score += 15
elif total > 5_000_000: score += 10
elif total > 1_000_000: score += 5

```
signals = m.get('corporate_insider_signals', 0) or 0
if signals > 20: score += 10
elif signals > 10: score += 6
elif signals > 5: score += 3

return min(score, 100)
```

def update_flags(m):
flags = []
if (m.get(‘corporate_insider_signals’, 0) or 0) > 5:
flags.append(‘trade’)
total = m.get(‘total_raised’, 0) or 1
pac = m.get(‘pac_contributions’, 0) or 0
if (pac / total) > 0.4:
flags.append(‘donor’)
m[‘flags’] = flags

# ─── MAIN ────────────────────────────────────────────────────────────────────

if **name** == ‘**main**’:
members = load_members()
if not members:
exit(1)

```
print(f'Starting Production v2.5.0 Split Run: {len(members)} members...')
leaderboard = []
missing_report = []

for i, m in enumerate(members):
    bid = m.get('id') or m.get('bioguide_id')
    name = m.get('name', '')
    state = m.get('state', '')
    chamber = m.get('chamber', '')
    office = 'S' if chamber == 'Senate' else 'H'

    print(f'  [{i+1}/{len(members)}] {name} ({bid})')

    # 1. Corporate Insider Signals (EDGAR Form 4)
    m['corporate_insider_signals'] = fetch_edgar_signals(name)
    m['edgar_signal_type'] = 'corporate_insider'

    # 2. FEC Campaign Finance
    if FEC_KEY and state:
        cand = fetch_fec_candidate(name, state, office)
        if cand.get('candidate_id'):
            m['fec_candidate_id'] = cand['candidate_id']
            m.update(fetch_fec_totals(cand['candidate_id']))
            total = m.get('total_raised', 0) or 0
            if total >= 1_000_000:
                m['total_raised_display'] = f'${total / 1_000_000:.1f}M'
            elif total >= 1000:
                m['total_raised_display'] = f'${total / 1000:.0f}K'
            else:
                m['total_raised_display'] = f'${total}'
        else:
            missing_report.append(f'FEC miss: {name} ({bid})')

    # 3. Score and flags
    m['score'] = compute_score(m)
    update_flags(m)
    m['data_updated'] = datetime.now().isoformat()

    # 4. SPLIT LOGIC
    # Load existing detail file (may already have votes from fetch_votes.py)
    detail_data = load_detail(bid)

    # Write ALL current member data into detail file.
    # This preserves any votes/other data already written by other pipelines.
    detail_data.update(m)
    detail_data['last_updated'] = m['data_updated']
    save_detail(bid, detail_data)

    # 5. Strip to lightweight leaderboard entry
    light_entry = {k: v for k, v in m.items() if k in LIGHT_FIELDS}
    leaderboard.append(light_entry)

# Save lightweight main list
with open(OUTPUT_FILE, 'w') as f:
    json.dump(leaderboard, f, indent=2, default=str)

print(f'\n✓ Production v2.5.0 Split Complete.')
print(f'  members.json: {len(leaderboard)} entries (grid-only fields)')
print(f'  details/: {len(leaderboard)} member files')

if missing_report:
    print(f'\n  FEC misses ({len(missing_report)}):')
    for line in missing_report:
        print(f'    {line}')
```
