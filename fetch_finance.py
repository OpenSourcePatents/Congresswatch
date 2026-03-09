"""
CongressWatch — Finance & Insider Signal Fetcher (Production v3.5)

Pulls:
• FEC campaign finance totals
• SEC EDGAR Form 4 insider filing signals
• Computes anomaly score inputs

Architecture:
Grid data → members.json (lightweight leaderboard)
Full member data → data/details/{bioguideId}.json

Other pipelines (votes, bills, etc.) can write to detail files and this
script will preserve those fields when merging.
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
    "User-Agent": "CongressWatch/1.0 (public-interest-research; mailto:project.congress.watch@gmail.com)",
    "Accept-Encoding": "gzip, deflate"
}

FEC_BASE = "https://api.open.fec.gov/v1"

OUTPUT_FILE = "data/members.json"
DETAILS_DIR = "data/details"

CIK_MAP_FILE = "data/manual_cik_map.json"
CIK_REVIEW_FILE = "data/unresolved_cik_candidates.json"

os.makedirs(DETAILS_DIR, exist_ok=True)

LIGHT_FIELDS = {
    "id","bioguide_id","name","party","state","district","chamber",
    "photo_url","term_start","score","flags",
    "corporate_insider_signals",
    "total_raised_display",
    "missed_votes_pct","votes_with_party_pct",
    "govtrack_id","data_updated",
    "edgar_status","edgar_cik"
}

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def sleep(s=1.2):
    time.sleep(s)

def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return default

def save_json(path, data):
    with open(path,"w") as f:
        json.dump(data,f,indent=2)

def load_members():
    try:
        with open(OUTPUT_FILE) as f:
            return json.load(f)
    except Exception as e:
        print("Could not load members.json:", e)
        return []

def load_detail(bid):
    path = os.path.join(DETAILS_DIR, f"{bid}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_detail(bid,data):
    path = os.path.join(DETAILS_DIR,f"{bid}.json")
    with open(path,"w") as f:
        json.dump(data,f,indent=2)

# ─────────────────────────────────────────────────────────────
# CIK RESOLUTION
# ─────────────────────────────────────────────────────────────

def normalize_name(name):
    name = name.lower().strip()
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b","",name)
    name = re.sub(r"[^a-z\s]"," ",name)
    return re.sub(r"\s+"," ",name).strip()

def name_aliases(name):
    parts = name.split()
    if len(parts)<2:
        return [name]
    first,last = parts[0],parts[-1]
    return list(dict.fromkeys([
        name,
        f"{first} {last}",
        f"{last}, {first}"
    ]))

def sec_search(query):
    url = (
        "https://efts.sec.gov/LATEST/search-index"
        f"?q={requests.utils.quote(query)}"
        "&forms=4&dateRange=custom&startdt=2023-01-01"
    )
    sleep(1.2)
    r = requests.get(url,headers=HEADERS,timeout=20)
    r.raise_for_status()
    return r.json()

def resolve_member_cik(member):

    manual = load_json(CIK_MAP_FILE,{})
    bid = member.get("id") or member.get("bioguide_id")

    if bid in manual:
        return {
            "status":"verified_manual",
            "cik":manual[bid]["cik"]
        }

    candidates = defaultdict(int)

    for alias in name_aliases(member["name"]):
        try:
            data = sec_search(f'"{alias}"')
        except:
            continue

        hits = data.get("hits",{}).get("hits",[])

        for h in hits:
            source = h.get("_source",{})
            cik = str(source.get("cik","")).strip()
            name = str(source.get("display_names","")).strip()

            if cik.isdigit():
                candidates[cik]+=1

    if not candidates:
        return {"status":"unresolved","cik":None}

    best = sorted(candidates.items(),key=lambda x:-x[1])[0]

    if best[1] >= 3:
        return {"status":"verified_auto","cik":best[0]}

    review = load_json(CIK_REVIEW_FILE,{})
    review[bid] = {
        "name":member["name"],
        "candidates":dict(candidates)
    }
    save_json(CIK_REVIEW_FILE,review)

    return {"status":"needs_review","cik":None}

# ─────────────────────────────────────────────────────────────
# EDGAR SIGNALS
# ─────────────────────────────────────────────────────────────

def fetch_edgar_signals(member):

    res = resolve_member_cik(member)

    member["edgar_status"] = res["status"]
    member["edgar_cik"] = res["cik"]

    if not res["cik"]:
        print("    EDGAR:",res["status"])
        return 0

    try:
        payload = sec_search(res["cik"])
        hits = payload.get("hits",{}).get("hits",[])
        count = len(hits)

        print(f"    EDGAR Hit: {count} filings via CIK {res['cik']}")
        return count

    except:
        member["edgar_status"]="query_failed"
        return 0

# ─────────────────────────────────────────────────────────────
# FEC
# ─────────────────────────────────────────────────────────────

def fetch_fec_candidate(name,state,office):

    parts=name.split()
    fec_name=f"{parts[-1]}, {' '.join(parts[:-1])}"

    params={
        "api_key":FEC_KEY,
        "q":fec_name,
        "state":state,
        "office":office,
        "per_page":3
    }

    try:
        sleep(0.5)
        r=requests.get(f"{FEC_BASE}/candidates/search/",params=params,headers=HEADERS)
        r.raise_for_status()
        res=r.json().get("results",[])
        return res[0] if res else {}
    except:
        return {}

def fetch_fec_totals(cid):

    params={
        "api_key":FEC_KEY,
        "candidate_id":cid,
        "cycle":2026,
        "per_page":1
    }

    try:
        sleep(0.5)
        r=requests.get(f"{FEC_BASE}/candidates/totals/",params=params,headers=HEADERS)
        r.raise_for_status()

        res=r.json().get("results",[])

        if res:
            r=res[0]
            return {
                "total_raised":r.get("receipts",0),
                "pac_contributions":r.get("contributions_from_other_committees",0)
            }
    except:
        pass

    return {}

# ─────────────────────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────────────────────

def compute_score(m):

    score=0
    total=m.get("total_raised",0)

    if total>20000000: score+=20
    elif total>10000000: score+=15
    elif total>5000000: score+=10
    elif total>1000000: score+=5

    signals=m.get("corporate_insider_signals",0)

    if signals>20: score+=10
    elif signals>10: score+=6
    elif signals>5: score+=3

    return min(score,100)

def update_flags(m):

    flags=[]

    if (m.get("corporate_insider_signals",0) or 0)>5:
        flags.append("trade")

    total=m.get("total_raised",0)
    pac=m.get("pac_contributions",0)

    if total>0 and pac/total>0.4:
        flags.append("donor")

    m["flags"]=flags

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

if __name__=="__main__":

    members=load_members()
    if not members:
        exit(1)

    print("Starting Production v3.5 Run:",len(members),"members")

    leaderboard=[]

    for i,m in enumerate(members):

        bid=m.get("id") or m.get("bioguide_id")
        name=m.get("name","")
        state=m.get("state","")
        chamber=m.get("chamber","")

        office="S" if chamber=="Senate" else "H"

        print(f"[{i+1}/{len(members)}] {name} ({bid})")

        # EDGAR
        m["corporate_insider_signals"]=fetch_edgar_signals(m)

        # FEC
        cand=fetch_fec_candidate(name,state,office)

        if cand.get("candidate_id"):
            m["fec_candidate_id"]=cand["candidate_id"]
            m.update(fetch_fec_totals(cand["candidate_id"]))

        # score
        m["score"]=compute_score(m)
        update_flags(m)

        m["data_updated"]=datetime.now().isoformat()

        # load existing detail file
        detail_data=load_detail(bid)

        # SAFE MERGE (prevents wiping existing pipeline data)
        for k,v in m.items():
            if v is not None:
                detail_data[k]=v

        detail_data["last_updated"]=m["data_updated"]

        save_detail(bid,detail_data)

        light={k:v for k,v in m.items() if k in LIGHT_FIELDS}
        leaderboard.append(light)

    with open(OUTPUT_FILE,"w") as f:
        json.dump(leaderboard,f,indent=2)

    print("✓ Production v3.5 Complete")