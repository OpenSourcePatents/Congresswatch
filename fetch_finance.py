"""
CongressWatch — Finance & Trade Data Fetcher (Production v2.4.2)
Pulls: FEC donors, SEC EDGAR signals, GovTrack votes, anomaly scores
FIXED: James C. Justice "Empty Results" fall-through crash & Name Normalization
"""

import os
import json
import time
import re
import requests
from datetime import datetime

CONGRESS_KEY = os.environ.get('CONGRESS_API_KEY', '')
FEC_KEY = os.environ.get('FEC_API_KEY', 'DEMO_KEY')

# SEC-compliant headers using the official project email
HEADERS = {
    'User-Agent': 'CongressWatch/1.0 (public-interest-research; mailto:project.congress.watch@gmail.com)',
    'Accept-Encoding': 'gzip, deflate'
}

FEC_BASE = 'https://api.open.fec.gov/v1'
OUTPUT_FILE = 'data/members.json'

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
        with open(OUTPUT_FILE) as f:
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
    variations.append(f"{last}, {first[0]}.")
    if middle:
        variations.append(f"{first} {middle[0]}. {last}")
    return list(dict.fromkeys(variations))

def fetch_edgar_signals(member_name):
    variations = normalize_name_for_edgar(member_name)
    max_hits = 0
    best_var = ""
    for name_var in variations:
        try:
            query = name_var.replace(" ", "+")
            url = f"https://efts.sec.gov/LATEST/search-index?q=%22{query}%22&forms=4&dateRange=custom&startdt=2023-01-01"
            sleep(1.2)
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                data = r.json()
                hits = len(data.get('hits', {}).get('hits', []))
                if hits > max_hits:
                    max_hits = hits
                    best_var = name_var
        except Exception:
            continue
    if max_hits > 0:
        print(f"    EDGAR Hit: {max_hits} signals via '{best_var}'")
    return max_hits

def fetch_fec_candidate(name, state_full, office):
    clean_name = re.sub(r'\s+[A-Z]\.?\s+', ' ', name).strip()
    parts = clean_name.split()
    fec_name = f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) >= 2 else clean_name
    state_abbr = STATE_MAP.get(state_full, state_full)

    params = {'api_key': FEC_KEY, 'q': fec_name, 'state': state_abbr, 'office': office, 'per_page': 3}
    try:
        sleep(0.5)
        r = requests.get(f'{FEC_BASE}/candidates/search/', params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        results = r.json().get('results', [])
        return results[0] if results else {}
    except Exception:
        return {}

def fetch_fec_totals(candidate_id):
    params = {'api_key': FEC_KEY, 'candidate_id': candidate_id, 'cycle': 2026, 'per_page': 1}
    try:
        sleep(0.5)
        r = requests.get(f'{FEC_BASE}/candidates/totals/', params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        results = r.json().get('results', [])
        if results:
            res = results[0]
            return {
                'total_raised': res.get('receipts', 0),
                'total_spent': res.get('disbursements', 0),
                'pac_contributions': res.get('contributions_from_other_committees', 0),
                'individual_contributions': res.get('individual_itemized_contributions', 0),
                'cash_on_hand': res.get('cash_on_hand_end_period', 0),
            }
    except Exception:
        return {}
    return {}

def compute_score(member):
    score = 0
    total = member.get('total_raised', 0) or 1
    
    # Fundraising Original Weights
    if total > 20_000_000: score += 20
    elif total > 10_000_000: score += 15
    elif total > 5_000_000: score += 10
    elif total > 1_000_000: score += 5
    
    # Insider Signals Restored Weights
    signals = member.get('corporate_insider_signals', 0) or 0
    if signals > 20: score += 10
    elif signals > 10: score += 6
    elif signals > 5: score += 3
    
    return min(score, 100)

def update_flags(m):
    flags = []
    if (m.get('corporate_insider_signals', 0) or 0) > 5: flags.append('trade')
    
    total = m.get('total_raised', 0) or 1
    pac = m.get('pac_contributions', 0) or 0
    if (pac / total) > 0.4: flags.append('donor')
    
    m['flags'] = flags

if __name__ == "__main__":
    members = load_members()
    if not members: exit(1)

    print(f'Starting Production v2.4.2 Merger Run: {len(members)} members...')
    enriched = []
    missing_report = []

    for i, m in enumerate(members):
        name, state = m.get('name', ''), m.get('state', '')
        chamber = m.get('chamber', '')
        office = 'S' if chamber == 'Senate' else 'H'

        print(f'  [{i+1}/{len(members)}] {name}')

        # 1. Pull Corporate Insider Signals
        m['corporate_insider_signals'] = fetch_edgar_signals(name)
        m['edgar_signal_type'] = 'corporate_insider'

        # 2. Pull FEC
        if FEC_KEY and state:
            cand = fetch_fec_candidate(name, state, office)
            if cand.get('candidate_id'):
                m['fec_candidate_id'] = cand['candidate_id']
                m.update(fetch_fec_totals(cand['candidate_id']))
                
                total = m.get('total_raised', 0) or 0
                if total >= 1_000_000: m['total_raised_display'] = f'${total/1_000_000:.1f}M'
                elif total >= 1000: m['total_raised_display'] = f'${total/1000:.0f}K'
                else: m['total_raised_display'] = f'${total}'
            else:
                missing_report.append(f"FEC MISS: {name} ({state})")

        m['score'] = compute_score(m)
        update_flags(m) # Critical: Syncs data to visual UI
        m['data_updated'] = datetime.now().isoformat()
        enriched.append(m)

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(enriched, f, indent=2, default=str)

    print('\n--- NIGHTLY MISSING DATA REPORT ---')
    for line in missing_report: print(line)
    print(f'\n✓ Production v2.4.2 Merger Complete.')
