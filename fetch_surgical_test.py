"""
CongressWatch — Surgical Test (v2.3-TEST)
Limit: 10 members for rapid verification.
Enhanced: Robust logging and JSON validation for SEC EDGAR.
"""

import os
import json
import time
import re
import requests
from datetime import datetime

FEC_KEY = os.environ.get('FEC_API_KEY', 'DEMO_KEY')
FEC_BASE = 'https://api.open.fec.gov/v1'

# SEC-compliant headers using the official project email
HEADERS = {
    'User-Agent': 'CongressWatch/1.0 (public-interest-research; mailto:project.congress.watch@gmail.com)',
    'Accept-Encoding': 'gzip, deflate'
}

# ── TEST CONFIG ────────────────────
TEST_LIMIT = 10
OUTPUT_FILE = 'data/surgical_test_results.json'
# ───────────────────────────────────

STATE_MAP = {
    'Alabama': 'AL', 'Alaska': 'AK', 'Arizona': 'AZ', 'Arkansas': 'AR', 'California': 'CA',
    'Colorado': 'CO', 'Connecticut': 'CT', 'Delaware': 'DE', 'Florida': 'FL', 'Georgia': 'GA',
    'Hawaii': 'HI', 'Idaho': 'ID', 'Illinois': 'IL', 'Indiana': 'IN', 'Iowa': 'IA',
    'Kansas': 'KS', 'Kentucky': 'KY', 'Louisiana': 'LA', 'Maine': 'ME', 'Maryland': 'MD',
    'Massachusetts': 'MA', 'Michigan': 'MI', 'Minnesota': 'MN', 'Mississippi': 'MS', 'Missouri': 'MO',
    'Montana': 'MT', 'Nebraska': 'NE', 'Nevada': 'NV', 'New Hampshire': 'NH', 'New Jersey': 'NJ',
    'New Mexico': 'NM', 'New York': 'NY', 'North Carolina': 'NC', 'North Dakota': 'ND', 'Ohio': 'OH',
    'Oklahoma': 'OK', 'Oregon': 'OR', 'Pennsylvania': 'PA', 'Rhode Island': 'RI', 'South Carolina': 'SC',
    'South Dakota': 'SD', 'Tennessee': 'TN', 'Texas': 'TX', 'Utah': 'UT', 'Vermont': 'VT',
    'Virginia': 'VA', 'Washington': 'WA', 'West Virginia': 'WV', 'Wisconsin': 'WI', 'Wyoming': 'WY',
    'District of Columbia': 'DC'
}

def sleep(s=1.2):
    time.sleep(s)

def load_members():
    try:
        with open('data/members.json') as f:
            return json.load(f)
    except:
        return []

def normalize_name_for_edgar(name):
    variations = []
    parts = name.strip().split()
    if len(parts) < 2: return [name]
    first, last = parts[0], parts[-1]
    middle = parts[1] if len(parts) > 2 else ""
    variations.append(f"{first} {last}")
    variations.append(f"{last}, {first}")
    variations.append(f"{first[0]}. {last}")
    if middle:
        variations.append(f"{first} {middle[0]}. {last}")
    return list(dict.fromkeys(variations))

def fetch_edgar_trades(member_name):
    variations = normalize_name_for_edgar(member_name)
    for name_var in variations:
        try:
            query = name_var.replace(" ", "+")
            url = f"https://efts.sec.gov/LATEST/search-index?q=%22{query}%22&forms=4&dateRange=custom&startdt=2023-01-01"
            sleep(1.2)
            r = requests.get(url, headers=HEADERS, timeout=15)
            
            if r.status_code != 200:
                print(f"    EDGAR {r.status_code} for {name_var}")
                continue

            try:
                data = r.json()
                hits = data.get('hits', {}).get('hits', [])
                if hits: 
                    print(f"    EDGAR Hit: {name_var} ({len(hits)})")
                    return len(hits)
            except ValueError:
                print(f"    EDGAR invalid JSON for {name_var}")
                continue

        except Exception as e:
            print(f"    EDGAR fail for {name_var}: {str(e)}")
            continue
    return 0

def fetch_fec_candidate(name, state_full, office):
    # Strip middle initials for better FEC matching
    clean_name = re.sub(r'\s+[A-Z]\.?\s+', ' ', name).strip()
    parts = clean_name.split()
    fec_name = f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) >= 2 else clean_name
    state_abbr = STATE_MAP.get(state_full, state_full)
    params = {'api_key': FEC_KEY, 'q': fec_name, 'state': state_abbr, 'office': office, 'per_page': 3}
    try:
        sleep(0.6)
        r = requests.get(f'{FEC_BASE}/candidates/search/', params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        results = r.json().get('results', [])
        return results[0] if results else {}
    except Exception as e: 
        print(f"    FEC fail for {name}: {str(e)}")
        return {}

def fetch_fec_totals(candidate_id):
    params = {'api_key': FEC_KEY, 'candidate_id': candidate_id, 'cycle': 2026, 'per_page': 1}
    try:
        sleep(0.6)
        r = requests.get(f'{FEC_BASE}/candidates/totals/', params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        results = r.json().get('results', [])
        if results:
            res = results[0]
            return {'total_raised': res.get('receipts', 0), 'cash_on_hand': res.get('cash_on_hand_end_period', 0)}
    except Exception: return {}
    return {}

if __name__ == "__main__":
    members = load_members()
    if not members: exit(1)
    
    # Surgical test focuses on the first 10 members
    members = members[:TEST_LIMIT]
    print(f'Starting Surgical Test: {len(members)} members...')
    enriched = []
    missing_report = []

    for i, m in enumerate(members):
        name, state, chamber = m.get('name'), m.get('state'), m.get('chamber')
        office = 'S' if chamber == 'Senate' else 'H'
        print(f'  [{i+1}/{len(members)}] {name}')
        
        m['edgar_trade_count'] = fetch_edgar_trades(name)
        
        if state:
            cand = fetch_fec_candidate(name, state, office)
            if cand.get('candidate_id'):
                m['fec_candidate_id'] = cand['candidate_id']
                m.update(fetch_fec_totals(cand['candidate_id']))
                print(f"    FEC Hit: {cand['candidate_id']}")
            else:
                missing_report.append(f"FEC MISS: {name} ({state})")
        
        enriched.append(m)

    os.makedirs('data', exist_ok=True)
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(enriched, f, indent=2, default=str)
    
    print('\n--- MISSING DATA REFERENCE ---')
    for line in missing_report: print(line)
    print(f'\n✓ Test complete. Results: {OUTPUT_FILE}')
