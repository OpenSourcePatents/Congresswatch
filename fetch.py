import os
import json
import time
import requests
from datetime import datetime

CONGRESS_KEY = os.environ.get('CONGRESS_API_KEY', '')
BASE = 'https://api.congress.gov/v3'
HEADERS = {'User-Agent': 'CongressWatch/1.0'}

os.makedirs('data', exist_ok=True)

def sleep(s=0.5):
    time.sleep(s)

def get_photo_url(bioguide_id):
    if not bioguide_id:
        return ''
    letter = bioguide_id[0].upper()
    return f'https://bioguide.congress.gov/bioguide/photo/{letter}/{bioguide_id}.jpg'

def normalize_chamber(raw_chamber, district):
    c = (raw_chamber or '').strip().lower()
    if c in ('senate', 'senator', 's', 'upper'):
        return 'Senate'
    if c in ('house', 'representative', 'h', 'lower', 'house of representatives'):
        return 'House'
    dist = str(district or '').strip()
    if dist and dist not in ('0', ''):
        return 'House'
    return 'Senate'

def normalize_party(raw_party):
    p = (raw_party or '').strip().lower()
    if 'democrat' in p or p == 'd':
        return 'Democratic'
    if 'republican' in p or p == 'r':
        return 'Republican'
    if 'independent' in p or p == 'i':
        return 'Independent'
    return raw_party or 'Unknown'

def fetch_chamber(chamber_param):
    params = {
        'api_key': CONGRESS_KEY,
        'limit': 250,
        'currentMember': 'true',
        'chamber': chamber_param,
        'offset': 0,
    }
    all_members = []
    while True:
        try:
            sleep(0.5)
            r = requests.get(f'{BASE}/member', params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            batch = r.json().get('members', [])
            if not batch:
                break
            all_members.extend(batch)
            print(f'{chamber_param}: fetched {len(all_members)} so far...')
            if len(batch) < 250:
                break
            params['offset'] += 250
        except Exception as e:
            print(f'Error fetching {chamber_param}: {e}')
            break
    return all_members

def normalize(m, forced_chamber):
    bid = m.get('bioguideId', '')
    name = m.get('name', '')
    if ',' in name:
        parts = name.split(',', 1)
        name = f'{parts[1].strip()} {parts[0].strip()}'
    district = m.get('district', '')
    party = normalize_party(m.get('partyName', ''))
    term_start = ''
    terms = m.get('terms', {})
    if isinstance(terms, dict):
        items = terms.get('item', [])
        if isinstance(items, list) and items:
            term_start = items[0].get('startYear', '') or items[0].get('start', '')
    elif isinstance(terms, list) and terms:
        term_start = terms[0].get('startYear', '') or terms[0].get('start', '')
    if term_start and len(str(term_start)) == 4:
        term_start = f'{term_start}-01-01'
    return {
        'id': bid,
        'name': name,
        'party': party,
        'state': m.get('state', ''),
        'district': str(district) if district else '',
        'chamber': forced_chamber,
        'photo_url': get_photo_url(bid),
        'term_start': str(term_start),
        'score': 0,
        'flags': [],
        'updated': datetime.now().isoformat(),
    }

print('Fetching Senate...')
senate_raw = fetch_chamber('Senate')
print(f'Got {len(senate_raw)} Senate members')

print('Fetching House...')
house_raw = fetch_chamber('House')
print(f'Got {len(house_raw)} House members')

seen = {}

for m in senate_raw:
    bid = m.get('bioguideId', '')
    if bid:
        seen[bid] = normalize(m, 'Senate')

for m in house_raw:
    bid = m.get('bioguideId', '')
    if bid and bid not in seen:
        seen[bid] = normalize(m, 'House')

all_members = list(seen.values())

senate_count = sum(1 for m in all_members if m['chamber'] == 'Senate')
house_count = sum(1 for m in all_members if m['chamber'] == 'House')

print(f'Total: {len(all_members)} | Senate: {senate_count} | House: {house_count}')

with open('data/members.json', 'w') as f:
    json.dump(all_members, f, indent=2)

print('Saved data/members.json')
