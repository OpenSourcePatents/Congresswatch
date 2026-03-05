import os
import json
import requests
from datetime import datetime

CONGRESS_KEY = os.environ.get('CONGRESS_API_KEY', '')
FEC_KEY = os.environ.get('FEC_API_KEY', 'DEMO_KEY')
LEGISCAN_KEY = os.environ.get('LEGISCAN_API_KEY', '')

BASE = 'https://api.congress.gov/v3'
HEADERS = {'User-Agent': 'CongressWatch/1.0'}

def fetch_members(chamber):
    params = {
        'api_key': CONGRESS_KEY,
        'limit': 250,
        'currentMember': 'true',
        'chamber': chamber,
    }
    try:
        r = requests.get(f'{BASE}/member', params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.json().get('members', [])
    except Exception as e:
        print(f'Error fetching {chamber}: {e}')
        return []

def get_photo_url(bioguide_id):
    if not bioguide_id:
        return ''
    letter = bioguide_id[0].upper()
    return f'https://bioguide.congress.gov/bioguide/photo/{letter}/{bioguide_id}.jpg'

def normalize(m, chamber):
    bid = m.get('bioguideId', '')
    name = m.get('name', '')
    # Congress.gov returns "Last, First" format
    if ',' in name:
        parts = name.split(',', 1)
        name = f'{parts[1].strip()} {parts[0].strip()}'
    return {
        'id':        bid,
        'name':      name,
        'party':     m.get('partyName', ''),
        'state':     m.get('state', ''),
        'district':  str(m.get('district', '')),
        'chamber':   chamber,
        'photo_url': get_photo_url(bid),
        'score':     0,
        'flags':     [],
        'updated':   datetime.now().isoformat(),
    }

print('Fetching House members...')
house = fetch_members('House')
print(f'  Got {len(house)} House members')

print('Fetching Senate members...')
senate = fetch_members('Senate')
print(f'  Got {len(senate)} Senate members')

all_members = []
for m in house:
    all_members.append(normalize(m, 'House'))
for m in senate:
    all_members.append(normalize(m, 'Senate'))

print(f'Total: {len(all_members)} members')

import os
os.makedirs('data', exist_ok=True)
with open('data/members.json', 'w') as f:
    json.dump(all_members, f, indent=2)

print('Saved to data/members.json')
