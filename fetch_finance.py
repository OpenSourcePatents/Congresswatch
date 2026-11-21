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
import math
from collections import defaultdict
import requests
from datetime import datetime

from travel_attribution import is_member_travel

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

# Whitelist of keys copied from the computed member dict onto members.json rows.
# missed_votes_pct / alec_match_count are NOT here on purpose: recompute_scores.py
# (finalize) owns promoting those from detail files.
LIGHT_FIELDS = {
    "id","bioguide_id","name","party","state","district","chamber",
    "photo_url","term_start","score","flags",
    "score_updated","score_components","score_methodology",
    "corporate_insider_signals",
    "total_raised",
    "pac_contributions","individual_contributions",
    "govtrack_id","data_updated",
    "edgar_status","edgar_cik","edgar_data_status","edgar_checked","edgar_updated","finance_status","finance_updated","finance_checked","roster_updated","fec_cycle","edgar_identity_status","edgar_identity_cik","edgar_identity_evidence","edgar_identity_note","edgar_legacy_status"
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

def eligible_sec_signals(member):
    """Only independently evidenced identity plus successful data may affect scoring.

    Raw candidate counts remain stored for investigation, never identity proof.
    Keep this contract aligned with OSP-API and index.html.
    """
    cik = member.get("edgar_identity_cik")
    evidence = member.get("edgar_identity_evidence")
    count = member.get("corporate_insider_signals")
    if (member.get("edgar_identity_status") == "verified"
            and isinstance(cik, str) and cik.strip() and cik == member.get("edgar_cik")
            and isinstance(evidence, list) and evidence
            and all(isinstance(url, str) and url.startswith("https://") for url in evidence)
            and member.get("edgar_data_status") == "available"
            and member.get("edgar_status") != "query_failed"
            and type(count) in (int, float) and math.isfinite(count) and count >= 0):
        return count
    return 0


def annotate_sec_identity(member):
    """Quarantine legacy automatic verification without deleting source evidence."""
    if member.get("edgar_status") == "verified_auto":
        member.setdefault("edgar_legacy_status", "verified_auto")
        member["edgar_status"] = "unverified_auto"
    member.setdefault("edgar_identity_status", "unverified")
    member.setdefault("edgar_identity_evidence", [])
    if member["edgar_identity_status"] != "verified":
        member["edgar_identity_note"] = "Name-search candidates are not verified member identities; excluded from scoring and activity flags."


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

# Run-level tally so a total SEC outage can abort the publish step instead of
# silently zeroing every member's insider signals (which is what a bare
# except here hid from 2026-04 onward).
SEC_STATS = {"ok": 0, "fail": 0}

def sec_search(query, ciks=None):
    # Rolling 2-year window; EDGAR FTS custom ranges need both bounds
    from datetime import timedelta
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
    url = (
        "https://efts.sec.gov/LATEST/search-index"
        f"?q={requests.utils.quote(query)}"
        f"&forms=4&dateRange=custom&startdt={start}&enddt={end}"
    )
    if ciks:
        # Proper entity filter — without it a CIK-digits query merely
        # full-text-matches the digits anywhere in any document.
        url += f"&ciks={requests.utils.quote(str(ciks))}"
    sleep(1.2)
    r = requests.get(url,headers=HEADERS,timeout=20)
    r.raise_for_status()
    payload = r.json()
    if (not isinstance(payload, dict) or not isinstance(payload.get("hits"), dict)
            or not isinstance(payload["hits"].get("hits"), list)):
        raise ValueError("Invalid SEC search response; not a verified empty result")
    SEC_STATS["ok"] += 1
    return payload

def resolve_member_cik(member):

    manual = load_json(CIK_MAP_FILE,{})
    bid = member.get("id") or member.get("bioguide_id")

    entry = manual.get(bid, {})
    evidence = entry.get("evidence", [])
    cik = entry.get("cik")
    if (entry.get("identity_status") == "verified" and isinstance(cik, str)
            and cik.strip() and isinstance(evidence, list) and evidence
            and all(isinstance(url, str) and url.startswith("https://") for url in evidence)):
        member["edgar_identity_status"] = "verified"
        member["edgar_identity_cik"] = cik
        member["edgar_identity_evidence"] = evidence
        member.pop("edgar_identity_note", None)
        return {"status": "verified_manual", "cik": cik}

    # A legacy manual CIK with no reviewed evidence is not sufficient either.
    member["edgar_identity_status"] = "unverified"
    annotate_sec_identity(member)

    candidates = defaultdict(int)
    alias_failed = False

    for alias in name_aliases(member["name"]):
        try:
            data = sec_search(f'"{alias}"')
        except Exception as e:
            SEC_STATS["fail"] += 1
            alias_failed = True
            print(f"    SEC search failed for {alias!r}: {e}")
            continue

        hits = data.get("hits",{}).get("hits",[])

        for h in hits:
            source = h.get("_source",{})
            # EDGAR FTS has no '_source.cik' — it returns 'ciks', a list of
            # zero-padded digit strings. Reading the singular key is why
            # every member sat at "unresolved" for months.
            for cik in (source.get("ciks") or []):
                cik = str(cik).strip()
                if cik.isdigit():
                    candidates[cik]+=1

    # A partial alias search cannot establish absence or a reliable identity.
    # Do not replace a previous CIK/count using incomplete candidate evidence.
    if alias_failed:
        return {"status": "query_failed", "cik": None}

    if not candidates:
        return {"status":"unresolved","cik":None}

    best = sorted(candidates.items(),key=lambda x:-x[1])[0]

    review = load_json(CIK_REVIEW_FILE,{})
    review[bid] = {
        "name":member["name"],
        "candidates":dict(candidates),
        "method": "Name-search frequency, not independently verified identity",
        "checked": datetime.now().isoformat()
    }
    save_json(CIK_REVIEW_FILE,review)

    if best[1] >= 3:
        return {"status":"unverified_auto","cik":best[0]}

    return {"status":"needs_review","cik":None}

# ─────────────────────────────────────────────────────────────
# EDGAR SIGNALS
# ─────────────────────────────────────────────────────────────

def fetch_edgar_signals(member):

    res = resolve_member_cik(member)

    prior_cik = member.get("edgar_cik")
    member["edgar_checked"] = datetime.now().isoformat()
    member["edgar_status"] = res["status"]
    if res["status"] == "query_failed":
        member["edgar_data_status"] = "stale" if prior_cik and member.get("corporate_insider_signals") is not None else "unavailable"
        return None
    member["edgar_cik"] = res["cik"]

    if not res["cik"]:
        member["edgar_data_status"] = "unresolved"
        print("    EDGAR:",res["status"])
        return 0

    try:
        payload = sec_search(res["cik"], ciks=res["cik"])
        # hits.hits is capped (~100/page); hits.total.value is the real count
        total = (payload.get("hits",{}).get("total",{}) or {}).get("value")
        if total is None:
            total = len(payload.get("hits",{}).get("hits",[]))
        count = int(total)

        if count < 0:
            raise ValueError("Negative SEC filing count")
        member["edgar_data_status"] = "available"
        member["edgar_updated"] = member["edgar_checked"]
        print(f"    EDGAR Hit: {count} filings via CIK {res['cik']}")
        return count

    except Exception as e:
        # None = "query failed, keep previous value" — distinct from a real 0
        SEC_STATS["fail"] += 1
        print(f"    EDGAR query failed: {e}")
        member["edgar_status"]="query_failed"
        # Retained counts must remain paired with the identity they came from.
        member["edgar_cik"] = prior_cik
        member["edgar_data_status"] = "stale" if prior_cik and member.get("corporate_insider_signals") is not None else "unavailable"
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
        r=requests.get(f"{FEC_BASE}/candidates/search/",params=params,headers=HEADERS, timeout=30)
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
        r=requests.get(f"{FEC_BASE}/candidates/totals/",params=params,headers=HEADERS, timeout=30)
        r.raise_for_status()

        res=r.json().get("results",[])

        if res:
            # Pick the most recent cycle that actually reports receipts
            res = sorted(res, key=lambda x: x.get("cycle") or 0, reverse=True)
            best = next((x for x in res if x.get("receipts")), res[0])
            return {
                "total_raised":best.get("receipts",0),
                # /candidates/totals/ field names (verified live 2026-08):
                # PAC money is 'other_political_committee_contributions';
                # individual money is only exposed as
                # 'individual_itemized_contributions' on this endpoint.
                "pac_contributions":best.get("other_political_committee_contributions",0) or 0,
                "individual_contributions":best.get("individual_itemized_contributions",0) or 0,
                "total_spent":float(best.get("disbursements") or 0),
                # arrives as a string ('42587451.00') on this endpoint
                "cash_on_hand":float(best.get("cash_on_hand_end_period") or 0),
                "fec_cycle":best.get("cycle")
            }
    except Exception as e:
        print(f"    FEC totals failed: {e}")

    return {}

# ─────────────────────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────────────────────

# Congressional base salary by year — keep in sync with CONGRESSIONAL_SALARY in
# index.html. Flat at 174,000 since 2009; earlier years matter for long-serving
# members' cumulative-salary baseline.
SALARY_BY_YEAR = {
    1998:136700, 1999:136700, 2000:141300, 2001:145100, 2002:150000,
    2003:154700, 2004:158100, 2005:162100, 2006:165200, 2007:165200,
    2008:169300,
}
ANNUAL_SALARY = 174000  # 2009-present

# Delegates and the Resident Commissioner are seated in the House but cannot
# vote on final passage, so a "missed vote" is meaningless for them and the
# attendance signal must be skipped entirely. The member schema carries no
# role/type field, so they are identified by state + chamber.
#
# EXACT set membership only, never a substring test: "Virgin Islands" is a
# substring collision with "Virginia" and "West Virginia", and a substring
# match would silently de-score every member from both states.
NON_VOTING_STATES = frozenset({
    "District of Columbia",
    "Puerto Rico",
    "Virgin Islands",
    "Guam",
    "American Samoa",
    "Northern Mariana Islands",
})


def is_non_voting_delegate(member):
    """True for House delegates / the Resident Commissioner, who cannot vote
    on final passage. Upstream vote data for them is inconsistent (some show
    0% missed, some 100%), so exclusion is identity-based, never data-based.
    """
    if not member:
        return False
    return ((member.get("state") or "") in NON_VOTING_STATES
            and (member.get("chamber") or "") == "House")


# Attendance scoring: minimum sample before the signal is scored at all,
# and the missed-vote ratio at which the full 5-point penalty applies.
# fetch_votes.VOTE_WINDOW (100) is the upstream sample size.
MIN_VOTES_FOR_ATTENDANCE = 20
ATTENDANCE_SATURATION_PCT = 25

def cumulative_salary(start_year, end_year):
    """Sum of base salaries for start_year..end_year inclusive (mirrors the
    frontend's computeCareerEarnings)."""
    return sum(SALARY_BY_YEAR.get(y, ANNUAL_SALARY) for y in range(start_year, end_year + 1))

def compute_score_components(m, detail=None):
    """
    The six weighted signals behind the anomaly score, as a dict.

    Max weights: trade timing 25, wealth gap 25, donor-vote 20,
                 ALEC similarity 15, foreign travel 10, attendance 5
    (sums to 100). compute_score() is just the clamped sum of these —
    this function exists so the breakdown is a first-class stored fact
    rather than something consumers have to reverse-engineer.
    """
    detail = detail or {}
    from datetime import timedelta

    # 1. Stock trade timing (25 max) — SEC EDGAR signals + PTR trade frequency
    trade_score = 0
    signals = eligible_sec_signals(m)
    if signals >= 3:
        trade_score += 25
    elif signals == 2:
        trade_score += 18
    elif signals == 1:
        trade_score += 10

    trades = detail.get("trades", []) or []
    if trades:
        cutoff = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        # transaction_date may be ISO or legacy MM/DD/YYYY — normalize first.
        # Scanned-PDF placeholder records have no transaction_date at all;
        # parse_date_any('') -> '' which never clears the cutoff.
        recent = sum(1 for t in trades
                     if cutoff <= parse_date_any(t.get("transaction_date")) <= datetime.now().strftime("%Y-%m-%d"))
        if recent >= 20:
            trade_score += 15
        elif recent >= 10:
            trade_score += 10
        elif recent >= 5:
            trade_score += 5
    trade_timing = min(trade_score, 25)

    # 2. Personal wealth cannot be inferred from campaign receipts. Keep the
    # public key for compatibility, but suspend this component until sourced
    # personal asset/liability disclosures support an explicit methodology.
    wealth_gap = 0

    # 3. Donor-vote alignment (20 max) — from bills pipeline
    donor_alignment = min(20, round((detail.get("donor_alignment_score", 0) or 0) * 0.2))

    # 4. Bill authorship / ALEC similarity (15 max) — from bills pipeline
    max_alec_sim = 0
    for bill in (detail.get("bills", []) or []):
        alec = bill.get("alec_match")
        if alec and alec.get("similarity_score", 0) > max_alec_sim:
            max_alec_sim = alec["similarity_score"]
        # raw best similarity (also stored below the 0.80 match threshold)
        raw_sim = bill.get("alec_best_similarity", 0) or 0
        if raw_sim > max_alec_sim:
            max_alec_sim = raw_sim
    if max_alec_sim >= 0.8:
        alec_similarity = 15
    elif max_alec_sim >= 0.65:
        alec_similarity = 11
    elif max_alec_sim >= 0.5:
        alec_similarity = 8
    elif max_alec_sim >= 0.35:
        alec_similarity = 4
    else:
        alec_similarity = 0

    # 5. Foreign travel (10 max) — from travel pipeline
    foreign_travel = 0
    travel = detail.get("travel", []) or []
    if travel:
        cutoff_2y = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
        recent_trips = sum(1 for t in travel
                           if is_member_travel(t)
                           and cutoff_2y <= parse_date_any(t.get("departure_date")) <= datetime.now().strftime("%Y-%m-%d"))
        if recent_trips >= 5:
            foreign_travel = 10
        elif recent_trips >= 3:
            foreign_travel = 7
        elif recent_trips >= 1:
            foreign_travel = 3

    # 6. Attendance (5 max) — missed votes ratio.
    # Linear to a saturation point of 25% missed: at >=25% the full 5-point
    # penalty applies. The old *50 multiplier maxed out at just 10% missed,
    # which pinned most members at 5 and made the signal meaningless. The
    # frontend's >15% "high absence" flag (index.html) is a warning tier
    # deliberately below this saturation point.
    # Samples under 20 votes are not scored at all: with a 20-vote window
    # every ratio is a multiple of 5%, which is noise, not signal.
    # Non-voting delegates are exempt: they cannot cast the votes they would
    # be penalized for missing.
    attendance = 0
    votes = detail.get("votes", []) or []
    if (not is_non_voting_delegate(m)
            and len(votes) >= MIN_VOTES_FOR_ATTENDANCE):
        missed = sum(1 for v in votes if ((v.get("position") or "").lower() in
                     ("not voting", "absent", "")))
        missed_pct = missed * 100.0 / len(votes)
        attendance = min(5, round(missed_pct * 5.0 / ATTENDANCE_SATURATION_PCT))

    return {
        "trade_timing": trade_timing,          # max 25
        "wealth_gap": wealth_gap,              # max 25
        "donor_alignment": donor_alignment,    # max 20
        "alec_similarity": alec_similarity,    # max 15
        "foreign_travel": foreign_travel,      # max 10
        "attendance": attendance,              # max 5
    }

def compute_score(m, detail=None):
    """Full 6-signal weighted anomaly score (0-100) — the clamped sum of
    compute_score_components(). Return type is an int, as callers expect."""
    return min(sum(compute_score_components(m, detail).values()), 100)

def update_flags(m):

    flags=[]

    if eligible_sec_signals(m)>5:
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

        # load existing detail file first (has votes, bills from other
        # pipelines, plus last-known finance values for failure fallback)
        detail_data=load_detail(bid)

        # Hydrate historical identity/count as one pair. Never carry a detail
        # count with an unrelated or missing roster CIK on refresh failure.
        if (m.get("edgar_cik") is None or m.get("corporate_insider_signals") is None):
            if detail_data.get("edgar_cik") and detail_data.get("corporate_insider_signals") is not None:
                for field in ("edgar_cik", "corporate_insider_signals", "edgar_status",
                              "edgar_data_status", "edgar_updated"):
                    if field in detail_data:
                        m[field] = detail_data[field]

        # EDGAR — None means "query failed": keep the last-known paired value
        signals=fetch_edgar_signals(m)
        if signals is None:
            signals=m.get("corporate_insider_signals")
        m["corporate_insider_signals"]=signals

        # FEC — on any failure, fall back to last-known totals so the
        # wealth-gap signal doesn't silently collapse to 0
        m["finance_checked"] = datetime.now().isoformat()
        m["finance_status"] = "fetch_failed"
        cand=fetch_fec_candidate(name,state,office)

        if cand.get("candidate_id"):
            m["fec_candidate_id"]=cand["candidate_id"]
            totals = fetch_fec_totals(cand["candidate_id"])
            m.update(totals)
            m["finance_status"] = "available" if totals else "fetch_failed"
            if totals:
                m["finance_updated"] = datetime.now().isoformat()
        elif detail_data.get("fec_candidate_id"):
            m["fec_candidate_id"]=detail_data["fec_candidate_id"]
        for field in ("total_raised","pac_contributions","individual_contributions"):
            if field not in m and field in detail_data:
                m[field]=detail_data[field]

        record_updated = datetime.now().isoformat()

        # SAFE MERGE (prevents wiping existing pipeline data)
        for k,v in m.items():
            if v is not None:
                detail_data[k]=v

        # Score — pass detail_data so we can use votes + bills data.
        # This is a best-effort score from whatever is on disk at 5am; the
        # inputs (votes/trades/travel/bills) are refreshed by other crons, so
        # recompute_scores.py re-derives it at 12pm and that run is the
        # authoritative one. Components are written alongside the score so the
        # two can never disagree with each other in between.
        components=compute_score_components(m, detail_data)
        m["score"]=min(sum(components.values()), 100)
        detail_data["score"]=m["score"]
        detail_data["score_components"]=components
        m["score_components"] = components
        m["score_updated"] = record_updated
        m["score_methodology"] = "activity-v4-bill-integrity"
        detail_data["score_updated"] = m["score_updated"]
        detail_data["score_methodology"] = m["score_methodology"]

        update_flags(m)
        detail_data["flags"]=m["flags"]

        detail_data["last_updated"] = record_updated

        save_detail(bid,detail_data)

        light={k:v for k,v in m.items() if k in LIGHT_FIELDS}
        leaderboard.append(light)

    # A run where every single SEC request failed must not publish zeroed
    # insider signals as if they were findings. (Detail files already keep
    # last-known values via the signals-is-None fallback.)
    if SEC_STATS["fail"] and SEC_STATS["ok"] == 0:
        raise SystemExit(
            f"ABORT: all {SEC_STATS['fail']} SEC EDGAR requests failed — "
            "not publishing zeroed insider signals")

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
    total_insider=sum(eligible_sec_signals(m) for m in final_members)

    stats = {
        "total_members": len(final_members),
        "members_with_scores": scored,
        "high_anomaly": high_anomaly,
        "total_insider_signals": total_insider,
        "raw_sec_candidate_filings": sum(m.get("corporate_insider_signals") or 0 for m in final_members),
        "last_updated": datetime.now().isoformat()
    }
    save_json("data/stats.json", stats)

    print(f"✓ Production v3.5 Complete — {scored}/{len(final_members)} members scored")
    if SEC_STATS["fail"]:
        raise SystemExit(f"Incomplete SEC refresh: {SEC_STATS['fail']} failed requests; affected members retain stale values.")
