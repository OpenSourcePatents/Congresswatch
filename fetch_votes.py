"""
CongressWatch — Vote History Fetcher (GovTrack v3)
Pulls: Recent 20 votes per member from GovTrack.us API
REPLACES: Retired ProPublica API
OPTIMIZATION: Reuses existing govtrack_id from members.json to save 500+ API calls.
NO API KEY REQUIRED
"""

import os
import json
import time
import requests
from datetime import datetime

MEMBERS_FILE = 'data/members.json'
DETAILS_DIR = 'data/details'

os.makedirs(DETAILS_DIR, exist_ok=True)

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def load_detail(bid):
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

# ─── GOVTRACK ────────────────────────────────────────────────────────────────

def get_govtrack_id(bioguide_id):
    """Maps Bioguide ID to GovTrack Person ID via GovTrack API.
    Only called if govtrack_id is missing from members.json.
    """
    bid = bioguide_id.strip()
    ua = 'CongressWatch/1.0 (public-interest-research; mailto:project.congress.watch@gmail.com)'
    url = f'https://www.govtrack.us/api/v2/person?bioguide_id={bid}'
    try:
        r = requests.get(url, headers={'User-Agent': ua}, timeout=10)
        if r.status_code == 429:
            print(f'    [!] Rate limited on crosswalk. Sleeping 10s...')
            time.sleep(10)
            r = requests.get(url, headers={'User-Agent': ua}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            objects = data.get('objects', [])
            if objects:
                return objects[0]['id']
            else:
                print(f'    [!] 200 OK but empty objects for {bid} - response: {data}')
        else:
            print(f'    [!] HTTP {r.status_code} for bioguide {bid} - {r.text[:200]}')
    except Exception as e:
        print(f'    [!] Exception for {bid}: {type(e).__name__}: {e}')
    return None

def fetch_member_votes(gt_id):
    """Fetches 20 most recent votes for a GovTrack person ID."""
    url = (
        f'https://www.govtrack.us/api/v2/vote_voter'
        f'?person={gt_id}&limit=20&sort=-created'
    )
    try:
        time.sleep(1.0)  # Throttle for GovTrack public API
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            return r.json().get('objects', [])
    except Exception as e:
        print(f'    Fail for GovTrack ID {gt_id}: {e}')
    return []

def format_vote(v):
    """Normalize a raw GovTrack vote_voter object to frontend-ready dict."""
    return {
        'bill': v['vote']['question'],
        'date': v['vote']['created'].split('T')[0],
        'position': v['option']['value'],
        'result': v['vote']['result'],
        'chamber': v['vote']['chamber_label'],
        'url': f"https://www.govtrack.us/congress/votes/{v['vote']['id']}"
    }

# ─── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    try:
        with open(MEMBERS_FILE, 'r') as f:
            members = json.load(f)
    except Exception as e:
        print(f'Critical Error: Could not load {MEMBERS_FILE}: {e}')
        exit(1)

    print(f'Starting Vote Pipeline v3 (GovTrack): {len(members)} members...')

    success = 0
    skipped = 0
    failed = 0

    for i, m in enumerate(members):
        bid = m.get('id') or m.get('bioguide_id')
        name = m.get('name', bid)

        if not bid:
            skipped += 1
            continue

        print(f'  [{i+1}/{len(members)}] {name}')

        # OPTIMIZATION: reuse govtrack_id already in members.json if present.
        # This avoids ~535 crosswalk API calls on subsequent runs.
        gt_id = m.get('govtrack_id') or get_govtrack_id(bid)

        if not gt_id:
            print(f'    Skip: No GovTrack mapping for {bid}')
            skipped += 1
            continue

        # Fetch votes
        raw_votes = fetch_member_votes(gt_id)

        if not raw_votes:
            print(f'    No votes returned for GovTrack ID {gt_id}')
            failed += 1
            continue

        # Format votes
        votes = []
        for v in raw_votes:
            try:
                votes.append(format_vote(v))
            except (KeyError, TypeError):
                continue  # Skip malformed entries silently

        # Merge into existing detail file — preserves finance data from fetch_finance.py
        detail_data = load_detail(bid)
        detail_data['votes'] = votes
        detail_data['govtrack_id'] = gt_id
        detail_data['votes_updated'] = datetime.now().isoformat()
        save_detail(bid, detail_data)

        print(f'    {len(votes)} votes saved')
        success += 1

    print(f'\n✓ Vote Pipeline v3 Complete.')
    print(f'  Success: {success}  |  Skipped: {skipped}  |  Failed: {failed}')
    print(f'  Data stored in {DETAILS_DIR}/')