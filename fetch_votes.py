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
from urllib.parse import urlencode

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

# ─── ID CROSSWALK ────────────────────────────────────────────────────────────

CROSSWALK_CACHE = 'data/crosswalk.json'
CROSSWALK_TTL_DAYS = 7

def build_crosswalk():
    """Download bioguide->govtrack mapping from unitedstates/congress-legislators.
    Caches to data/crosswalk.json with 7-day TTL.
    Returns dict: {bioguide_id: govtrack_id}
    """
    # Use cache if fresh
    if os.path.exists(CROSSWALK_CACHE):
        age_days = (datetime.now().timestamp() - os.path.getmtime(CROSSWALK_CACHE)) / 86400
        if age_days < CROSSWALK_TTL_DAYS:
            with open(CROSSWALK_CACHE, 'r') as f:
                cached = json.load(f)
            print(f'  Crosswalk loaded from cache ({len(cached)} entries, {age_days:.1f}d old)')
            return cached

    ua = 'CongressWatch/1.0 (public-interest-research; mailto:project.congress.watch@gmail.com)'
    urls = [
        'https://unitedstates.github.io/congress-legislators/legislators-current.json',
        'https://unitedstates.github.io/congress-legislators/legislators-historical.json',
    ]
    crosswalk = {}
    for url in urls:
        try:
            r = requests.get(url, headers={'User-Agent': ua}, timeout=30)
            if r.status_code == 200:
                for legislator in r.json():
                    ids = legislator.get('id', {})
                    bio = ids.get('bioguide')
                    gt = ids.get('govtrack')
                    if bio and gt:
                        crosswalk[bio] = gt
                print(f'  Crosswalk loaded {len(crosswalk)} entries from {url}')
            else:
                print(f'  [!] Crosswalk HTTP {r.status_code} for {url}')
        except Exception as e:
            print(f'  [!] Crosswalk error: {e}')

    if crosswalk:
        with open(CROSSWALK_CACHE, 'w') as f:
            json.dump(crosswalk, f)
        print(f'  Crosswalk cached to {CROSSWALK_CACHE}')

    return crosswalk

def fetch_member_votes(gt_id):
    """Fetches 20 most recent votes for a GovTrack person ID.
    Includes exponential backoff on 429.
    """
    params = {'person': gt_id, 'limit': 20, 'sort': '-created'}
    url = 'https://www.govtrack.us/api/v2/vote_voter?' + urlencode(params)
    for attempt in range(3):
        try:
            time.sleep(1.0)
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                objects = r.json().get('objects', [])
                if not objects:
                    print(f'    [!] 200 OK but 0 objects for GT ID {gt_id} - response: {r.text[:300]}')
                return objects
            elif r.status_code == 429:
                wait = 2 ** attempt * 5
                print(f'    [!] 429 rate limited. Sleeping {wait}s (attempt {attempt+1}/3)...')
                time.sleep(wait)
            else:
                print(f'    [!] HTTP {r.status_code} for GT ID {gt_id} - {r.text[:200]}')
                return []
        except Exception as e:
            print(f'    Fail for GovTrack ID {gt_id}: {e}')
            return []
    print(f'    [!] All retries exhausted for GT ID {gt_id}')
    return []

def format_vote(v):
    """Normalize a raw GovTrack vote_voter object to frontend-ready dict."""
    return {
        'bill': v['vote']['question'],
        'question_text': v['vote'].get('question_text', ''),
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

    # Build bioguide->govtrack crosswalk from congress-legislators (2 HTTP calls total)
    crosswalk = build_crosswalk()
    print(f'  Crosswalk ready: {len(crosswalk)} total legislators mapped.')

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

        # Use govtrack_id from members.json if present, else look up crosswalk
        gt_id = m.get('govtrack_id') or crosswalk.get(bid.strip())

        if not gt_id:
            print(f'    Skip: No GovTrack mapping for {bid}')
            skipped += 1
            continue

        # Fetch votes
        raw_votes = fetch_member_votes(gt_id)

        if not raw_votes:
            print(f'    No votes returned for GovTrack ID {gt_id}')
            detail_data = load_detail(bid)
            detail_data['votes_status'] = 'no_recent_votes'
            detail_data['govtrack_id'] = gt_id
            detail_data['votes_updated'] = datetime.now().isoformat()
            save_detail(bid, detail_data)
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
        detail_data['votes_status'] = 'ok'
        detail_data['govtrack_id'] = gt_id
        detail_data['votes_updated'] = datetime.now().isoformat()
        save_detail(bid, detail_data)

        print(f'    {len(votes)} votes saved')
        success += 1

    total = success + failed + skipped
    rate = success / total * 100 if total else 0
    print(f'\n✓ Vote Pipeline v3 Complete.')
    print(f'  Success: {success}  |  Skipped: {skipped}  |  Failed: {failed}')
    print(f'  Success rate: {rate:.1f}%')
    print(f'  Data stored in {DETAILS_DIR}/')