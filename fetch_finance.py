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
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

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
    "total_raised","total_raised_display",
    "pac_contributions","individual_contributions",
    "missed_votes_pct","votes_with_party_pct",
    "govtrack_id","data_updated",
    "edgar_status","edgar_cik"
}

# FEC expects 2-letter postal codes; members.json stores full state names
STATE_ABBREV = {
    "Alabama":"AL","Alaska":"AK","Arizona":"AZ","Arkansas":"AR","California":"CA",
    "Colorado":"CO","Connecticut":"CT","Delaware":"DE","Florida":"FL","Georgia":"GA",
    "Hawaii":"HI","Idaho":"ID","Illinois":"IL","Indiana":"IN","Iowa":"IA",
    "Kansas":"KS","Kentucky":"KY","Louisiana":"LA","Maine":"ME","Maryland":"MD",
    "Massachusetts":"MA","Michigan":"MI","Minnesota":"MN","Mississippi":"MS","Missouri":"MO",
    "Montana":"MT","Nebraska":"NE","Nevada":"NV","New Hampshire":"NH","New Jersey":"NJ",
    "New Mexico":"NM","New York":"NY","North Carolina":"NC","North Dakota":"ND","Ohio":"OH",
    "Oklahoma":"OK","Oregon":"OR","Pennsylvania":"PA","Rhode Island":"RI","South Carolina":"SC",
    "South Dakota":"SD","Tennessee":"TN","Texas":"TX","Utah":"UT","Vermont":"VT",
    "Virginia":"VA","Washington":"WA","West Virginia":"WV","Wisconsin":"WI","Wyoming":"WY",
    "District of Columbia":"DC","Puerto Rico":"PR","Guam":"GU","American Samoa":"AS",
    "Virgin Islands":"VI","Northern Mariana Islands":"MP",
}

def state_code(state):
    if not state:
        return ""
    if len(state) == 2:
        return state.upper()
    return STATE_ABBREV.get(state, "")

def parse_date_any(s):
    """Normalize a date string (ISO or MM/DD/YYYY) to YYYY-MM-DD; '' if unparseable."""
    if not s:
        return ""
    s = str(s).strip()[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def sleep(s=1.2):
    time.sleep(s)

def load_json(path, default):
    """Guarded load: a missing/empty file yields default; a corrupt existing
    file ABORTS the run so we never overwrite good data with a partial view."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        raise SystemExit(f"ABORT: {path} exists but failed to parse ({e}). "
                         "Refusing to continue — fix or remove the file.")

def save_json(path, data):
    """Atomic write: temp file + os.replace so a crash never truncates data."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)

def load_members():
    members = load_json(OUTPUT_FILE, [])
    if not members:
        print("Could not load members.json (missing or empty)")
    return members

def load_detail(bid):
    return load_json(os.path.join(DETAILS_DIR, f"{bid}.json"), {})

def save_detail(bid,data):
    save_json(os.path.join(DETAILS_DIR, f"{bid}.json"), data)

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
    # Rolling 2-year window; EDGAR FTS custom ranges need both bounds
    from datetime import timedelta
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
    url = (
        "https://efts.sec.gov/LATEST/search-index"
        f"?q={requests.utils.quote(query)}"
        f"&forms=4&dateRange=custom&startdt={start}&enddt={end}"
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

    except Exception as e:
        # None = "query failed, keep previous value" — distinct from a real 0
        print(f"    EDGAR query failed: {e}")
        member["edgar_status"]="query_failed"
        return None

# ─────────────────────────────────────────────────────────────
# FEC
# ─────────────────────────────────────────────────────────────

def fetch_fec_candidate(name,state,office):

    parts=name.split()
    fec_name=f"{parts[-1]}, {' '.join(parts[:-1])}"

    params={
        "api_key":FEC_KEY,
        "q":fec_name,
        "office":office,
        "per_page":3
    }
    # FEC wants the 2-letter postal code; a full state name is silently ignored
    code = state_code(state)
    if code:
        params["state"] = code

    try:
        sleep(0.5)
        r=requests.get(f"{FEC_BASE}/candidates/search/",params=params,headers=HEADERS)
        r.raise_for_status()
        res=r.json().get("results",[])
        # Prefer an exact state match over blind results[0]
        if code:
            for c in res:
                if (c.get("state") or "").upper() == code:
                    return c
        return res[0] if res else {}
    except Exception as e:
        print(f"    FEC candidate search failed: {e}")
        return {}

def fetch_fec_totals(cid):
    """Latest available cycle totals. Senators fundraise under their own
    (2028/2030) cycles, so a hardcoded cycle misses most of the Senate."""
    params={
        "api_key":FEC_KEY,
        "candidate_id":cid,
        "per_page":20
    }

    try:
        sleep(0.5)
        r=requests.get(f"{FEC_BASE}/candidates/totals/",params=params,headers=HEADERS)
        r.raise_for_status()

        res=r.json().get("results",[])

        if res:
            # Pick the most recent cycle that actually reports receipts
            res = sorted(res, key=lambda x: x.get("cycle") or 0, reverse=True)
            best = next((x for x in res if x.get("receipts")), res[0])
            return {
                "total_raised":best.get("receipts",0),
                "pac_contributions":best.get("contributions_from_other_committees",0),
                "individual_contributions":best.get("individual_contributions",0),
                "fec_cycle":best.get("cycle")
            }
    except Exception as e:
        print(f"    FEC totals failed: {e}")

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

    # 1. Stock trade timing (25 pts max) — SEC EDGAR + PTR trades
    trade_score = 0
    signals = m.get("corporate_insider_signals", 0) or 0
    if signals >= 3:
        trade_score += 25
    elif signals == 2:
        trade_score += 18
    elif signals == 1:
        trade_score += 10

    # PTR trade frequency bonus
    trades = detail.get("trades", []) or []
    if trades:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        # transaction_date may be ISO or legacy MM/DD/YYYY — normalize first
        recent = sum(1 for t in trades
                     if parse_date_any(t.get("transaction_date")) >= cutoff)
        if recent >= 20:
            trade_score += 15
        elif recent >= 10:
            trade_score += 10
        elif recent >= 5:
            trade_score += 5
    score += min(trade_score, 25)

    # 2. Wealth gap (25 pts max) — estimated gap vs salary
    total = m.get("total_raised", 0) or 0
    if total > 0:
        est_wealth = total * 0.45
        start_year = m.get("term_start", "2010")[:4]
        try:
            years_in_office = max(1, datetime.now().year - int(start_year))
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
        # raw best similarity (also stored below the 0.80 match threshold)
        raw_sim = bill.get("alec_best_similarity", 0) or 0
        if raw_sim > max_alec_sim:
            max_alec_sim = raw_sim
    if max_alec_sim >= 0.8:
        score += 15
    elif max_alec_sim >= 0.65:
        score += 11
    elif max_alec_sim >= 0.5:
        score += 8
    elif max_alec_sim >= 0.35:
        score += 4

    # 5. Foreign travel (10 pts max) — from travel pipeline
    travel = detail.get("travel", []) or []
    if travel:
        from datetime import timedelta
        cutoff_2y = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
        recent_trips = sum(1 for t in travel
                           if parse_date_any(t.get("departure_date")) >= cutoff_2y)
        if recent_trips >= 5:
            score += 10
        elif recent_trips >= 3:
            score += 7
        elif recent_trips >= 1:
            score += 3

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
# SUPABASE
# ─────────────────────────────────────────────────────────────

def supabase_update_finance(bid, m):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }
    data = {
        "score": m.get("score", 0),
        "flags": m.get("flags", []),
        "corporate_insider_signals": m.get("corporate_insider_signals", 0) or 0,
        "edgar_status": m.get("edgar_status", "unresolved"),
        "edgar_cik": m.get("edgar_cik"),
        "fec_candidate_id": m.get("fec_candidate_id"),
        "data_updated": datetime.now().isoformat()
    }
    # Only send finance figures we actually have — a missing value must not
    # PATCH a real prior value down to 0
    for field in ("total_raised", "pac_contributions", "individual_contributions"):
        if m.get(field) is not None and field in m:
            data[field] = float(m.get(field) or 0)
    try:
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/members?bioguide_id=eq.{bid}",
            headers=headers,
            json=data,
            timeout=15
        )
        if r.status_code not in (200, 204):
            print(f"  Supabase finance update failed: {r.status_code}")
    except Exception as e:
        print(f"  Supabase error: {e}")

def supabase_update_stats():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/update_stats",
            headers=headers,
            json={},
            timeout=15
        )
        if r.status_code in (200, 204):
            print("Supabase stats updated")
        else:
            print(f"Supabase stats update failed: {r.status_code}")
    except Exception as e:
        print(f"Supabase stats error: {e}")

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

        # load existing detail file first (has votes, bills from other
        # pipelines, plus last-known finance values for failure fallback)
        detail_data=load_detail(bid)

        # EDGAR — None means "query failed": keep the last-known value
        signals=fetch_edgar_signals(m)
        if signals is None:
            signals=detail_data.get("corporate_insider_signals", 0) or 0
        m["corporate_insider_signals"]=signals

        # FEC — on any failure, fall back to last-known totals so the
        # wealth-gap signal doesn't silently collapse to 0
        cand=fetch_fec_candidate(name,state,office)

        if cand.get("candidate_id"):
            m["fec_candidate_id"]=cand["candidate_id"]
            m.update(fetch_fec_totals(cand["candidate_id"]))
        elif detail_data.get("fec_candidate_id"):
            m["fec_candidate_id"]=detail_data["fec_candidate_id"]
        for field in ("total_raised","pac_contributions","individual_contributions"):
            if not m.get(field) and detail_data.get(field):
                m[field]=detail_data[field]

        m["data_updated"]=datetime.now().isoformat()

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

        try:
            supabase_update_finance(bid, m)
        except Exception as e:
            print(f"  Supabase finance error for {bid}: {e}")

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

    save_json(OUTPUT_FILE, final_members)

    scored=sum(1 for m in final_members if (m.get("score") or 0)>0)
    high_anomaly=sum(1 for m in final_members if (m.get("score") or 0)>=60)
    total_insider=sum(m.get("corporate_insider_signals",0) or 0 for m in final_members)

    stats = {
        "total_members": len(final_members),
        "members_with_scores": scored,
        "high_anomaly": high_anomaly,
        "total_insider_signals": total_insider,
        "last_updated": datetime.now().isoformat()
    }
    save_json("data/stats.json", stats)

    try:
        supabase_update_stats()
    except Exception as e:
        print(f"Supabase stats error: {e}")

    print(f"✓ Production v3.5 Complete — {scored}/{len(final_members)} members scored")