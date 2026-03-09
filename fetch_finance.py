"""
CongressWatch — Finance & Trade Data Fetcher (Production v3)
Pulls: FEC donors, SEC EDGAR signals, GovTrack stats, anomaly scores
SPLIT ARCHITECTURE: Grid data stays in members.json; Deep data moves to details/
"""

import os
import json
import time
import re
from collections import defaultdict
import requests
from datetime import datetime

CONGRESS_KEY = os.environ.get('CONGRESS_API_KEY', '')
FEC_KEY = os.environ.get('FEC_API_KEY', 'DEMO_KEY')
HEADERS = {
    'User-Agent': 'CongressWatch/1.0 (public-interest-research; mailto:project.congress.watch@gmail.com)',
    'Accept-Encoding': 'gzip, deflate'
}

FEC_BASE = 'https://api.open.fec.gov/v1'
OUTPUT_FILE = 'data/members.json'
DETAILS_DIR = 'data/details'
CACHE_DIR = 'data/cache'
CIK_MAP_FILE = 'data/manual_cik_map.json'
CIK_REVIEW_FILE = 'data/unresolved_cik_candidates.json'

os.makedirs(DETAILS_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# ─── INFRASTRUCTURE CONFIG ───────────────────────────────────────────────────
# ONLY these fields stay in members.json (leaderboard grid).
# Everything else goes to data/details/{bioguideId}.json.
# Add new grid-visible fields here intentionally — don't let the list grow by accident.
LIGHT_FIELDS = {
    'id', 'bioguide_id', 'name', 'party', 'state', 'district', 'chamber',
    'photo_url', 'term_start', 'score', 'flags', 'corporate_insider_signals',
    'total_raised_display', 'missed_votes_pct', 'votes_with_party_pct',
    'govtrack_id', 'data_updated',
    'edgar_status', 'edgar_cik'
}

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

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def sleep(s=1.2):
    time.sleep(s)

def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, payload):
    with open(path, 'w') as f:
        json.dump(payload, f, indent=2)

def load_members():
    try:
        with open(OUTPUT_FILE) as f:
            return json.load(f)
    except Exception as e:
        print(f'Critical Error: Could not load {OUTPUT_FILE}: {e}')
        return []

def load_detail(bid):
    """Load existing detail file for a member, or return empty dict."""
    detail_path = os.path.join(DETAILS_DIR, f'{bid}.json')
    if os.path.exists(detail_path):
        with open(detail_path, 'r') as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}

def save_detail(bid, data):
    detail_path = os.path.join(DETAILS_DIR, f'{bid}.json')
    with open(detail_path, 'w') as f:
        json.dump(data, f, indent=2)

def normalize_person_name(name):
    name = (name or '').lower().strip()
    name = re.sub(r'\b(jr|sr|ii|iii|iv|v)\b\.?', '', name)
    name = re.sub(r'[^a-z\s,-]', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip(' ,')
    return name

def member_name_aliases(name):
    clean = re.sub(r'\s+[A-Z]\.?(?=\s|$)', ' ', name or '').strip()
    parts = clean.split()
    if len(parts) < 2:
        return [clean] if clean else []
    first, last = parts[0], parts[-1]
    aliases = [
        clean,
        f'{first} {last}',
        f'{last}, {first}',
    ]
    return list(dict.fromkeys([a.strip() for a in aliases if a.strip()]))

def deep_find_values(obj, wanted_keys):
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in wanted_keys:
                found.append(v)
            found.extend(deep_find_values(v, wanted_keys))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(deep_find_values(item, wanted_keys))
    return found

# ─── EDGAR / CIK RESOLUTION ──────────────────────────────────────────────────

def sec_search(query, startdt='2023-01-01', forms='4'):
    url = (
        'https://efts.sec.gov/LATEST/search-index'
        f'?q={requests.utils.quote(query)}'
        f'&forms={forms}&dateRange=custom&startdt={startdt}'
    )
    sleep(1.2)
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()

def load_manual_cik_map():
    return load_json(CIK_MAP_FILE, {})

def save_manual_cik_map(data):
    save_json(CIK_MAP_FILE, data)

def append_unresolved_review(member, candidates):
    review = load_json(CIK_REVIEW_FILE, {})
    bid = member.get('id') or member.get('bioguide_id') or member.get('name')
    review[bid] = {
        'name': member.get('name'),
        'state': member.get('state'),
        'chamber': member.get('chamber'),
        'candidates': candidates,
        'updated': datetime.now().isoformat(),
    }
    save_json(CIK_REVIEW_FILE, review)

def candidate_score(member, candidate_name, hit_count):
    target_aliases = {normalize_person_name(a) for a in member_name_aliases(member.get('name', ''))}
    candidate_norm = normalize_person_name(candidate_name)
    if not candidate_norm:
        return 0

    score = 0

    if candidate_norm in target_aliases:
        score += 100

    target_parts = normalize_person_name(member.get('name', '')).replace(',', ' ').split()
    candidate_parts = candidate_norm.replace(',', ' ').split()

    if target_parts and candidate_parts:
        if target_parts[-1] == candidate_parts[-1]:
            score += 15
        if target_parts[0] == candidate_parts[0]:
            score += 10

    score += min(hit_count, 20)
    return score

def extract_cik_candidates(member, search_payload):
    buckets = defaultdict(lambda: {'hit_count': 0, 'names': set()})
    hits = search_payload.get('hits', {}).get('hits', [])

    for hit in hits:
        source = hit.get('_source', {}) if isinstance(hit, dict) else {}
        cik_values = deep_find_values(source, {'cik', 'entitycik', 'ownercik', 'reportingownercik'})
        name_values = deep_find_values(source, {'display_names', 'entityname', 'ownername', 'reportingownername', 'name'})

        cik_values = [str(v).strip() for v in cik_values if str(v).strip().isdigit()]

        flat_names = []
        for n in name_values:
            if isinstance(n, list):
                flat_names.extend([str(x).strip() for x in n if str(x).strip()])
            elif str(n).strip():
                flat_names.append(str(n).strip())

        for cik in cik_values:
            buckets[cik]['hit_count'] += 1
            buckets[cik]['names'].update(flat_names)

    ranked = []
    for cik, info in buckets.items():
        best_name = ''
        best_score = -1
        for candidate_name in (info['names'] or {''}):
            s = candidate_score(member, candidate_name, info['hit_count'])
            if s > best_score:
                best_score = s
                best_name = candidate_name

        ranked.append({
            'cik': str(cik).zfill(10),
            'name': best_name,
            'hit_count': info['hit_count'],
            'score': best_score,
            'all_names': sorted(info['names'])[:10],
        })

    ranked.sort(key=lambda x: (-x['score'], -x['hit_count'], x['cik']))
    return ranked

def resolve_member_cik(member):
    manual = load_manual_cik_map()
    bid = member.get('id') or member.get('bioguide_id') or member.get('name')

    if bid in manual and manual[bid].get('cik'):
        return {
            'status': 'verified_manual',
            'cik': str(manual[bid]['cik']).zfill(10),
            'name': manual[bid].get('name', member.get('name', '')),
        }

    all_candidates = []
    seen = set()

    for alias in member_name_aliases(member.get('name', '')):
        try:
            payload = sec_search(f'"{alias}"')
        except Exception:
            continue

        for c in extract_cik_candidates(member, payload):
            key = (c['cik'], c['name'])
            if key not in seen:
                seen.add(key)
                all_candidates.append(c)

    all_candidates.sort(key=lambda x: (-x['score'], -x['hit_count'], x['cik']))

    if not all_candidates:
        return {'status': 'unresolved', 'cik': None, 'candidates': []}

    top = all_candidates[0]
    runner_up = all_candidates[1] if len(all_candidates) > 1 else None

    exact_name = normalize_person_name(top['name']) in {
        normalize_person_name(a) for a in member_name_aliases(member.get('name', ''))
    }
    clear_margin = runner_up is None or (top['score'] - runner_up['score'] >= 25)

    if exact_name and clear_margin:
        manual[bid] = {
            'cik': top['cik'],
            'name': top['name'] or member.get('name', ''),
            'source': 'auto_exact_unique',
            'updated': datetime.now().isoformat(),
        }
        save_manual_cik_map(manual)
        return {'status': 'verified_auto', 'cik': top['cik'], 'name': top['name']}

    append_unresolved_review(member, all_candidates[:10])
    return {'status': 'needs_review', 'cik': None, 'candidates': all_candidates[:10]}

def fetch_edgar_signals(member):
    resolution = resolve_member_cik(member)
    member['edgar_status'] = resolution['status']
    member['edgar_cik'] = resolution.get('cik')
    member['edgar_matched_name'] = resolution.get('name')

    if not resolution.get('cik'):
        if resolution['status'] == 'needs_review':
            print('    EDGAR: needs review')
        elif resolution['status'] == 'unresolved':
            print('    EDGAR: unresolved')
        return 0

    try:
        payload = sec_search(resolution['cik'])
        hits = payload.get('hits', {}).get('hits', [])
        hit_count = len(hits)
        if hit_count > 0:
            print(
                f"    EDGAR Hit: {hit_count} filings via verified CIK {resolution['cik']} "
                f"({resolution['status']})"
            )
        else:
            print(f"    EDGAR: verified CIK {resolution['cik']} but no hits")
        return hit_count
    except Exception:
        member['edgar_status'] = 'query_failed'
        print('    EDGAR: query failed')
        return 0

# ─── FEC ─────────────────────────────────────────────────────────────────────

def fetch_fec_candidate(name, state_full, office):
    clean_name = re.sub(r'\s+[A-Z]\.?\s+', ' ', name).strip()
    parts = clean_name.split()
    fec_name = f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) >= 2 else clean_name
    state_abbr = STATE_MAP.get(state_full, state_full)
    params = {
        'api_key': FEC_KEY,
        'q': fec_name,
        'state': state_abbr,
        'office': office,
        'per_page': 3
    }
    try:
        sleep(0.5)
        r = requests.get(f'{FEC_BASE}/candidates/search/', params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        results = r.json().get('results', [])
        return results[0] if results else {}
    except Exception:
        return {}

def fetch_fec_totals(candidate_id):
    params = {
        'api_key': FEC_KEY,
        'candidate_id': candidate_id,
        'cycle': 2026,
        'per_page': 1
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
    except Exception:
        return {}
    return {}

# ─── SCORING ─────────────────────────────────────────────────────────────────

def compute_score(m):
    score = 0
    total = m.get('total_raised', 0) or 1
    if total > 20_000_000:
        score += 20
    elif total > 10_000_000:
        score += 15
    elif total > 5_000_000:
        score += 10
    elif total > 1_000_000:
        score += 5

    signals = m.get('corporate_insider_signals', 0) or 0
    if signals > 20:
        score += 10
    elif signals > 10:
        score += 6
    elif signals > 5:
        score += 3

    return min(score, 100)

def update_flags(m):
    flags = []
    if (m.get('corporate_insider_signals', 0) or 0) > 5:
        flags.append('trade')
    total = m.get('total_raised', 0) or 0
    pac = m.get('pac_contributions', 0) or 0
    if total > 0 and (pac / total) > 0.4:
        flags.append('donor')
    m['flags'] = flags

# ─── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    members = load_members()
    if not members:
        exit(1)

    if not os.path.exists(CIK_MAP_FILE):
        save_json(CIK_MAP_FILE, {})
    if not os.path.exists(CIK_REVIEW_FILE):
        save_json(CIK_REVIEW_FILE, {})

    print(f'Starting Production v3 Split Run: {len(members)} members...')
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
        m['corporate_insider_signals'] = fetch_edgar_signals(m)
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
        detail_data = load_detail(bid)
        detail_data.update(m)
        detail_data['last_updated'] = m['data_updated']
        save_detail(bid, detail_data)

        # 5. Strip to lightweight leaderboard entry
        light_entry = {k: v for k, v in m.items() if k in LIGHT_FIELDS}
        leaderboard.append(light_entry)

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(leaderboard, f, indent=2, default=str)

    print(f'\n✓ Production v3 Split Complete.')
    print(f'  members.json: {len(leaderboard)} entries (grid-only fields)')
    print(f'  details/: {len(leaderboard)} member files')

    if missing_report:
        print(f'\n  FEC misses ({len(missing_report)}):')
        for line in missing_report:
            print(f'    {line}')
