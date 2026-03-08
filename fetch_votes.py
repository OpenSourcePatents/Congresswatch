"""
CongressWatch — Vote History Fetcher (v1.0)
Pulls: Recent 20 votes per member from ProPublica Congress API
This is to populate the 'Votes' tab with real-time legislative activity.
"""

import os
import json
import time
import requests
from datetime import datetime

PROPUBLICA_KEY = os.environ.get('PROPUBLICA_API_KEY', '')
MEMBERS_FILE = 'data/members.json'
DETAILS_DIR = 'data/details'

# Ensure the details directory exists
os.makedirs(DETAILS_DIR, exist_ok=True)

def fetch_member_votes(bioguide_id):
    url = f"https://api.propublica.org/congress/v1/members/{bioguide_id}/votes.json"
    headers = {'X-API-Key': PROPUBLICA_KEY}
    
    try:
        # Throttling for ProPublica (5000 requests per day limit)
        time.sleep(0.5) 
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            data = r.json()
            # Return the first 20 votes
            return data.get('results', [{}])[0].get('votes', [])
        else:
            print(f"    Error {r.status_code} for {bioguide_id}")
            return []
    except Exception as e:
        print(f"    Fail for {bioguide_id}: {str(e)}")
        return []

if __name__ == "__main__":
    if not PROPUBLICA_KEY:
        print("Error: PROPUBLICA_API_KEY environment variable not set.")
        exit(1)

    try:
        with open(MEMBERS_FILE, 'r') as f:
            members = json.load(f)
    except Exception as e:
        print(f"Could not load members: {e}")
        exit(1)

    print(f"Starting Vote Fetch for {len(members)} members...")

    for i, m in enumerate(members):
        bid = m.get('id') or m.get('bioguide_id')
        if not bid: continue

        print(f"  [{i+1}/{len(members)}] {m.get('name')} ({bid})")
        
        votes = fetch_member_votes(bid)
        
        # Save to individual member detail file
        detail_path = f"{DETAILS_DIR}/{bid}.json"
        
        # Load existing deep data if it exists, otherwise start fresh
        detail_data = {}
        if os.path.exists(detail_path):
            with open(detail_path, 'r') as f:
                try: detail_data = json.load(f)
                except: detail_data = {}

        # Update only the votes section
        detail_data['votes'] = votes
        detail_data['last_updated'] = datetime.now().isoformat()

        with open(detail_path, 'w') as f:
            json.dump(detail_data, f, indent=2)

    print("\n✓ Vote Fetch Complete. Deep data stored in data/details/")
