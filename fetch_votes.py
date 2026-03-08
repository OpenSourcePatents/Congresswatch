"""
CongressWatch — Vote History Fetcher (GovTrack v1.0)
Pulls: Recent 20 votes per member from GovTrack.us API
REPLACES: Retired ProPublica API
NO API KEY REQUIRED
"""

import os
import json
import time
import requests
from datetime import datetime

MEMBERS_FILE = 'data/members.json'
DETAILS_DIR = 'data/details'

# Ensure the details directory exists for the infrastructure split
os.makedirs(DETAILS_DIR, exist_ok=True)

def get_govtrack_id(bioguide_id):
    """Maps Bioguide ID to GovTrack Person ID using GovTrack's person API"""
    url = f"https://www.govtrack.us/api/v2/person?bioguide_id={bioguide_id}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get('objects'):
                return data['objects'][0]['id']
    except Exception:
        pass
    return None

def fetch_member_votes(gt_id):
    """Fetches 20 most recent votes for a specific GovTrack ID"""
    url = f"https://www.govtrack.us/api/v2/vote_voter?person={gt_id}&limit=20&sort=-created"
    try:
        time.sleep(1.0) # Throttling for GovTrack public API
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            return r.json().get('objects', [])
    except Exception as e:
        print(f"    Fail for GovTrack ID {gt_id}: {str(e)}")
    return []

if __name__ == "__main__":
    try:
        with open(MEMBERS_FILE, 'r') as f:
            members = json.load(f)
    except Exception as e:
        print(f"Critical Error: Could not load members.json: {e}")
        exit(1)

    print(f"Starting Vote Pipeline (GovTrack Edition): {len(members)} members...")

    for i, m in enumerate(members):
        bid = m.get('id') or m.get('bioguide_id')
        if not bid: continue

        print(f"  [{i+1}/{len(members)}] {m.get('name')} ({bid})")
        
        # 1. Get GovTrack ID
        gt_id = get_govtrack_id(bid)
        if not gt_id:
            print(f"    Skip: No GovTrack mapping for {bid}")
            continue

        # 2. Fetch Votes
        votes = fetch_member_votes(gt_id)
        
        # 3. Store in detail file
        detail_path = f"{DETAILS_DIR}/{bid}.json"
        detail_data = {}
        if os.path.exists(detail_path):
            with open(detail_path, 'r') as f:
                try: detail_data = json.load(f)
                except: detail_data = {}

        # Format votes to be frontend-ready
        detail_data['votes'] = [{
            'bill': v['vote']['question'],
            'date': v['vote']['created'].split('T')[0],
            'position': v['option']['value'],
            'result': v['vote']['result'],
            'chamber': v['vote']['chamber_label'],
            'url': f"https://www.govtrack.us/congress/votes/{v['vote']['id']}"
        } for v in votes]
        
        detail_data['last_updated'] = datetime.now().isoformat()

        with open(detail_path, 'w') as f:
            json.dump(detail_data, f, indent=2)

    print("\n✓ Vote Pipeline Complete. Data stored in data/details/")
