"""
CongressWatch — Finance & Trade Data Fetcher (TEST VERSION 2.1)
Pulls: FEC donors, SEC EDGAR trades, GovTrack votes, anomaly scores
FIXED: Name flipping (Last, First), State Abbreviation mapping (added DC)
"""

import os
import json
import time
import requests
from datetime import datetime

CONGRESS_KEY = os.environ.get('CONGRESS_API_KEY', '')
FEC_KEY      = os.environ.get('FEC_API_KEY', 'DEMO_KEY')

FEC_BASE      = 'https://api.open.fec.gov/v1'
GOVTRACK_BASE = 'https://www.govtrack.us/api/v2'
HEADERS = {'User-Agent': 'CongressWatch/1.0 (public-interest-research)'}

# ── TEST MODE ──────────────────────
TEST_MODE = True
TEST_LIMIT = 5
TEST_MEMBERS = ['Mike Rounds'] 
# ───────────────────────────────────

# Mapping for FEC API "state" parameter
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

os.makedirs('data', exist_ok=True)

def sleep(s=0.5):
    time.sleep(s)

def load_members():
    try:
        with open('data/members.json') as f:
            return json.load(f)
    except:
        print('No members.json found — run fetch.py first')
        return []

# ══════════════════════════════════
# FEC — CAMPAIGN FINANCE
# ══════════════════════════════════

def fetch_fec_candidate(name, state_full, office):
    # 1. Flip name: "Mike Rounds" -> "Rounds, Mike"
    parts = name.strip().split()
    fec_name = f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) >= 2 else name
    
    # 2. Convert "South Dakota" -> "SD"
    state_abbr = STATE_MAP.get(state_full, state_full)

    params = {
        'api_key': FEC_KEY,
        'q': fec_name,
        'state': state_abbr,
        'office': office,
        'per_page': 3,
    }
    
    print(f'    FEC Query: q="{fec_name}" state={state_abbr} office={office}')
    
    try:
        sleep(0.5)
        r = requests.get(f'{FEC_BASE}/candidates/search/', params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        results = r.json().get('results', [])
        return results[0] if results else {}
    except Exception as e:
        print(f'  FEC candidate error {name}: {e}')
        return {}

def fetch_fec_totals(candidate_id):
    params = {
        'api_key': FEC_KEY,
        'candidate_id': candidate_id,
        'cycle': 2026,
        'per_page': 1,
    }
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
    except Exception as e:
        print(f'  FEC totals error {candidate_id}: {e}')
        return {}

def fetch_fec_top_donors(committee_id):
    params = {
        'api_key': FEC_KEY,
        'committee_id': committee_id,
        'two_year_transaction_period': 2026,
        'per_page': 10,
        'sort': '-total',
    }
    try:
        sleep(0.5)
        r = requests.get(f'{FEC_BASE}/schedules/schedule_a/by_contributor/', params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        results = r.json().get('results', [])
        return [
            {'name': d.get('contributor_name', ''), 'employer': d.get('contributor_employer', ''), 'amount': d.get('total', 0)}
            for d in results
        ]
    except Exception as e:
        return []

# ══════════════════════════════════
# STUBS FOR TESTING ONLY
# ══════════════════════════════════

def fetch_edgar_trades(member_name):
    return 0

def fetch_govtrack_person(bioguide_id):
    # STUB: Do not use in production
    return {'missed_votes_pct': 0.05, 'votes_with_party_pct': 92.0}

# ══════════════════════════════════
# MAIN
# ══════════════════════════════════

if __name__ == "__main__":
    members = load_members()
    if not members: exit(1)

    if TEST_MODE:
        priority = [m for m in members if m.get('name') in TEST_MEMBERS]
        others   = [m for m in members if m.get('name') not in TEST_MEMBERS]
        members  = (priority + others)[:TEST_LIMIT]

    print(f'Processing {len(members)} test members...')
    enriched = []

    for i, member in enumerate(members):
        name    = member.get('name', '')
        state   = member.get('state', '')
        chamber = member.get('chamber', '')
        office  = 'S' if chamber == 'Senate' else 'H'

        print(f'  [{i+1}/{len(members)}] {name}')

        if FEC_KEY and state:
            candidate = fetch_fec_candidate(name, state, office)
            if candidate:
                cand_id = candidate.get('candidate_id', '')
                comms   = candidate.get('principal_committees', [])
                comm_id = comms[0].get('committee_id', '') if comms else ''
                
                member['fec_candidate_id'] = cand_id
                member['fec_committee_id'] = comm_id
                print(f'    FEC: {cand_id} | Committee: {comm_id}')

                if cand_id:
                    totals = fetch_fec_totals(cand_id)
                    member.update(totals)
                    print(f'    Raised: ${member.get("total_raised", 0):,.0f}')

                if comm_id:
                    donors = fetch_fec_top_donors(comm_id)
                    member['top_donors_list'] = donors
                    member['top_donors'] = ', '.join([d['employer'] for d in donors[:2] if d['employer']])
            else:
                print('    FEC: No candidate found.')

        enriched.append(member)

    with open('data/members_test.json', 'w') as f:
        json.dump(enriched, f, indent=2, default=str)
    
    print('\n✓ Test Done. Running "cat" in workflow will show full data.')
