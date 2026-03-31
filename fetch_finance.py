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

ANNUAL_SALARY = 174000  # Congressional salary baseline

def compute_score(m, detail=None):
    """
    Full 6-signal weighted anomaly score (0-100).
    Weights: trade timing 25, wealth gap 25, donor-vote 20,
             bill authorship 15, foreign travel 10, attendance 5.
    """
    detail = detail or {}
    score = 0

    # 1. Stock trade timing (25 pts max) — SEC EDGAR insider signals
    signals = m.get("corporate_insider_signals", 0) or 0
    if signals >= 3:
        score += 25
    elif signals == 2:
        score += 18
    elif signals == 1:
        score += 10

    # 2. Wealth gap (25 pts max) — estimated gap vs salary
    total = m.get("total_raised", 0) or 0
    if total > 0:
        est_wealth = total * 0.45
        start_year = m.get("term_start", "2010")[:4]
        try:
            years_in_office = max(1, 2026 - int(start_year))
        except (ValueError, TypeError):
            years_in_office = 10
        cumulative_salary = years_in_office * ANNUAL_SALARY
        gap = est_wealth - cumulative_salary
        if gap > 5000000:
            score += 25
        elif gap > 2000000:
            score += 20
        elif gap > 500000:
            score += 15
        elif gap > 100000:
            score += 10
        elif gap > 0:
            score += 5

    # 3. Donor-vote alignment (20 pts max) — from bills pipeline
    donor_score = detail.get("donor_alignment_score", 0) or 0
    score += min(20, round(donor_score * 0.2))

    # 4. Bill authorship / ALEC similarity (15 pts max) — from bills pipeline
    bills = detail.get("bills", []) or []
    max_alec_sim = 0
    for bill in bills:
        alec = bill.get("alec_match")
        if alec and alec.get("similarity_score", 0) > max_alec_sim:
            max_alec_sim = alec["similarity_score"]
    if max_alec_sim >= 0.8:
        score += 15
    elif max_alec_sim >= 0.65:
        score += 11
    elif max_alec_sim >= 0.5:
        score += 8
    elif max_alec_sim >= 0.35:
        score += 4

    # 5. Foreign travel (10 pts max) — placeholder until PTR pipeline
    # score += 0

    # 6. Attendance (5 pts max) — missed votes ratio
    votes = detail.get("votes", []) or []
    if votes:
        missed = sum(1 for v in votes if (v.get("position", "").lower() in
                     ("not voting", "absent", "")))
        if len(votes) > 0:
            miss_ratio = missed / len(votes)
            score += min(5, round(miss_ratio * 50))

    return min(score, 100)

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

        m["data_updated"]=datetime.now().isoformat()

        # load existing detail file (has votes, bills from other pipelines)
        detail_data=load_detail(bid)

        # SAFE MERGE (prevents wiping existing pipeline data)
        for k,v in m.items():
            if v is not None:
                detail_data[k]=v

        # score — pass detail_data so we can use votes + bills data
        m["score"]=compute_score(m, detail_data)
        detail_data["score"]=m["score"]

        update_flags(m)
        detail_data["flags"]=m["flags"]

        detail_data["last_updated"]=m["data_updated"]

        save_detail(bid,detail_data)

        light={k:v for k,v in m.items() if k in LIGHT_FIELDS}
        leaderboard.append(light)

    # Safe merge into existing members.json — preserve fields from other pipelines
    existing_members=load_members()
    existing_by_id={em.get("id") or em.get("bioguide_id"):em for em in existing_members}

    for entry in leaderboard:
        eid=entry.get("id") or entry.get("bioguide_id")
        if eid and eid in existing_by_id:
            existing_by_id[eid].update(entry)
        elif eid:
            existing_by_id[eid]=entry

    final_members=list(existing_by_id.values())

    with open(OUTPUT_FILE,"w") as f:
        json.dump(final_members,f,indent=2)

    scored=sum(1 for m in final_members if (m.get("score") or 0)>0)
    print(f"✓ Production v3.5 Complete — {scored}/{len(final_members)} members scored")