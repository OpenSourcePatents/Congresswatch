"""
CongressWatch — Finance & Trade Data Fetcher (TEST VERSION)
Pulls: FEC donors, SEC EDGAR trades, GovTrack votes, anomaly scores
TEST MODE: Only processes first 5 members to conserve API limits
"""

import os
import json
import time
import requests
from datetime import datetime

CONGRESS_KEY = os.environ.get('CONGRESS_API_KEY', '')
FEC_KEY      = os.environ.get('FEC_API_KEY', 'DEMO_KEY')
LEGISCAN_KEY = os.environ.get('LEGISCAN_API_KEY', '')

FEC_BASE      = 'https://api.open.fec.gov/v1'
CONGRESS_BASE = 'https://api.congress.gov/v3'
GOVTRACK_BASE = 'https://www.govtrack.us/api/v2'
EDGAR_SEARCH  = 'https://efts.sec.gov/LATEST/search-index'

HEADERS = {'User-Agent': 'CongressWatch/1.0 (public-interest-research)'}

# ── TEST MODE ──────────────────────
TEST_MODE = True
TEST_LIMIT = 5
TEST_MEMBERS = ['Mike Rounds']  # Priority members to test first
# ───────────────────────────────────

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

def fetch_fec_candidate(name, state, office):
    params = {
        'api_key': FEC_KEY,
        'q': name,
        'state': state,
        'office': office,
        'per_page': 3,
        # 'is_active_candidate': True removed to capture all currently serving members
    }
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
        'cycle': 2026,  # Updated to current cycle
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
        'two_year_transaction_period': 2026,  # Updated to current cycle
        'per_page': 10,
        'sort': '-total',
    }
    try:
        sleep(0.5)
        r = requests.get(f'{FEC_BASE}/schedules/schedule_a/by_contributor/', params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        results = r.json().get('results', [])
        return [
            {
                'name': d.get('contributor_name', ''),
                'employer': d.get('contributor_employer', ''),
                'amount': d.get('total', 0),
            }
            for d in results
        ]
    except Exception as e:
        print(f'  FEC donors error {committee_id}: {e}')
    return []

# ══════════════════════════════════
# SEC EDGAR — STOCK TRADES
# ══════════════════════════════════

def fetch_edgar_trades(member_name):
    # Current implementation needs normalization layer — known issue
    try:
        sleep(1.0)
        search_url = f'https://efts.sec.gov/LATEST/search-index?q=%22{member_name.replace(" ", "+")}%22&forms=4&dateRange=custom&startdt=2023-01-01'
        r = requests.get(search_url, headers=HEADERS, timeout=20)
        if r.status_code == 200:
            data = r.json()
            hits = data.get('hits', {}).get('hits', [])
            return len(hits)
    except Exception as e:
        print(f'  EDGAR error {member_name}: {e}')
    return 0

# ══════════════════════════════════
# GOVTRACK — VOTING RECORDS
# ══════════════════════════════════

def fetch_govtrack_person(bioguide_id):
    try:
        sleep(0.5)
        r = requests.get(
            f'{GOVTRACK_BASE}/person',
            params={'bioguideid': bioguide_id},
            headers=HEADERS,
            timeout=15
        )
        if r.status_code == 200:
            data = r.json()
            objects = data.get('objects', [])
            if objects:
                p = objects[0]
                return {
                    'govtrack_id': p.get('id', ''),
                    'ideology_score': p.get('ideology_score', None),
                    'leadership_score': p.get('leadership_score', None),
                    'missed_votes_pct': p.get('missed_votes_pct', None),
                    'votes_with_party_pct': p.get('votes_with_party_pct', None),
                }
    except Exception as e:
        print(f'  GovTrack error {bioguide_id}: {e}')
    return {}

def fetch_govtrack_votes(govtrack_id):
    if not govtrack_id:
        return {}
    try:
        sleep(0.5)
        r = requests.get(
            f'{GOVTRACK_BASE}/vote_voter',
            params={'person': govtrack_id, 'limit': 200},
            headers=HEADERS,
            timeout=20
        )
        if r.status_code == 200:
            data = r.json()
            votes = data.get('objects', [])
            total = len(votes)
            return {
                'total_votes_sampled': total,
                'recent_vote_count': total,
            }
    except Exception as e:
        print(f'  GovTrack votes error {govtrack_id}: {e}')
    return {}

# ══════════════════════════════════
# ANOMALY SCORING
# ══════════════════════════════════

def compute_score(member):
    score = 0
    total = member.get('total_raised', 0) or 1
    pac   = member.get('pac_contributions', 0) or 0
    pac_ratio = pac / total

    if pac_ratio > 0.6:      score += 35
    elif pac_ratio > 0.4:    score += 25
    elif pac_ratio > 0.25:   score += 15
    elif pac_ratio > 0.1:    score += 5

    if total > 20_000_000:   score += 20
    elif total > 10_000_000: score += 15
    elif total > 5_000_000:  score += 10
    elif total > 1_000_000:  score += 5

    missed_pct = member.get('missed_votes_pct') or 0
    if missed_pct > 0.25:    score += 20
    elif missed_pct > 0.15:  score += 12
    elif missed_pct > 0.08:  score += 5

    party_pct = member.get('votes_with_party_pct') or 0
    if party_pct > 97:       score += 15
    elif party_pct > 93:     score += 8
    elif party_pct > 90:     score += 3

    trades = member.get('edgar_trade_count', 0) or 0
    if trades > 20:          score += 10
    elif trades > 10:        score += 6
    elif trades > 5:         score += 3

    return min(score, 100)

def get_flags(member):
    flags = []
    total_raised = member.get('total_raised', 0) or 1
    pac_ratio = (member.get('pac_contributions', 0) or 0) / max(total_raised, 1)

    if pac_ratio > 0.4:                                   flags.append('donor')
    if (member.get('missed_votes_pct') or 0) > 0.15:     flags.append('attendance')
    if (member.get('edgar_trade_count') or 0) > 5:       flags.append('trade')
    if (member.get('votes_with_party_pct') or 0) > 95:   flags.append('party')
    return flags

# ══════════════════════════════════
# MAIN
# ══════════════════════════════════

if __name__ == "__main__":
    print('Loading members...')
    members = load_members()
    if not members:
        exit(1)

    # ── TEST MODE: prioritize specific members, then fill to limit ──
    if TEST_MODE:
        priority = [m for m in members if m.get('name') in TEST_MEMBERS]
        others   = [m for m in members if m.get('name') not in TEST_MEMBERS]
        members  = (priority + others)[:TEST_LIMIT]
        print(f'TEST MODE: Processing {len(members)} members only')
        for m in members:
            print(f'  → {m.get("name")} ({m.get("chamber")}, {m.get("state")})')
        print()

    print(f'Processing {len(members)} members...')
    enriched = []

    for i, member in enumerate(members):
        name    = member.get('name', '')
        state   = member.get('state', '')
        chamber = member.get('chamber', '')
        bid     = member.get('id', '')
        office  = 'S' if chamber == 'Senate' else 'H'

        print(f'  [{i+1}/{len(members)}] {name}')

        # ── GovTrack (no key needed) ──
        govtrack = fetch_govtrack_person(bid)
        member.update(govtrack)

        # ── FEC Finance ──
        if FEC_KEY and state:
            candidate = fetch_fec_candidate(name, state, office)
            if candidate:
                cand_id = candidate.get('candidate_id', '')
                comms   = candidate.get('principal_committees', [])
                comm_id = comms[0].get('committee_id', '') if comms else ''

                member['fec_candidate_id'] = cand_id
                member['fec_committee_id'] = comm_id

                print(f'    FEC: {cand_id} / committee: {comm_id}')

                if cand_id:
                    totals = fetch_fec_totals(cand_id)
                    member.update(totals)
                    print(f'    Raised: {totals.get("total_raised", "none")}')

                    total = member.get('total_raised', 0) or 0
                    if total >= 1_000_000:
                        member['total_raised_display'] = f'${total/1_000_000:.1f}M'
                    elif total >= 1000:
                        member['total_raised_display'] = f'${total/1000:.0f}K'
                    else:
                        member['total_raised_display'] = f'${total:,.0f}'

                if comm_id:
                    donors = fetch_fec_top_donors(comm_id)
                    member['top_donors_list'] = donors
                    member['top_donors'] = ', '.join(
                        d['employer'] or d['name']
                        for d in donors[:3]
                        if d.get('employer') or d.get('name')
                    )
                    print(f'    Top donors: {member["top_donors"] or "none found"}')
            else:
                print(f'    FEC: no candidate found')

        # ── SEC EDGAR trades ──
        trade_count = fetch_edgar_trades(name)
        member['edgar_trade_count'] = trade_count
        member['total_trades'] = trade_count
        print(f'    EDGAR trades: {trade_count}')

        # ── Compute score ──
        member['score']        = compute_score(member)
        member['flags']        = get_flags(member)
        member['data_updated'] = datetime.now().isoformat()
        print(f'    Score: {member["score"]} | Flags: {member["flags"]}')

        enriched.append(member)

    # ── TEST MODE: save to separate file, don't overwrite members.json ──
    print(f'\nSaving test results...')
    with open('data/members_test.json', 'w') as f:
        json.dump(enriched, f, indent=2, default=str)

    print(f'\n✓ Test complete — saved to data/members_test.json (members.json untouched)')
    print(f'  Members tested: {len(enriched)}')
    for m in enriched:
        print(f'  {m["name"]}: score={m["score"]} raised={m.get("total_raised_display","–")} fec={m.get("fec_candidate_id","none")}')
