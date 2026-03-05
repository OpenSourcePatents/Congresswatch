"""
CongressWatch — Finance & Trade Data Fetcher
Pulls: FEC donors, SEC EDGAR trades, GovTrack votes, anomaly scores
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
        'is_active_candidate': True​​​​​​​​​​​​​​​​
