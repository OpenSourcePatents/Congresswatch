"""
donor_matcher.py — Match bill topics against member's donor industries.

Uses keyword sets per industry. Member's top donor industries come from
FEC vault data already written by fetch_finance.py.
"""

# ---------------------------------------------------------------------------
# Industry keyword map
# Expand these as donor data becomes richer.
# ---------------------------------------------------------------------------

INDUSTRY_KEYWORDS = {
    "Oil & Gas": [
        "energy", "fossil", "drilling", "pipeline", "fracking", "emissions",
        "petroleum", "natural gas", "crude", "refinery", "offshore", "epa",
        "carbon", "climate", "greenhouse", "coal", "lng", "methane",
        "oil_gas", "fossil_fuel", "energy_production", "fuel_standard"
    ],
    "Pharmaceuticals": [
        "drug", "pharmaceutical", "prescription", "medicare", "medicaid",
        "fda", "biologics", "generic", "patent", "clinical", "therapy",
        "medication", "formulary", "rebate", "drug_price", "drug_pricing",
        "biosimilar", "controlled_substance", "health_insurance"
    ],
    "Finance & Banking": [
        "bank", "financial", "credit", "loan", "mortgage", "interest",
        "securities", "investment", "hedge", "derivative", "dodd", "frank",
        "cfpb", "fdic", "federal_reserve", "capital_requirement", "fintech",
        "cryptocurrency", "crypto", "blockchain", "lending", "usury"
    ],
    "Defense & Military": [
        "defense", "military", "weapons", "arms", "pentagon", "contractor",
        "procurement", "national_security", "appropriation", "armed_forces",
        "navy", "army", "air_force", "marines", "intelligence", "warfare",
        "missile", "drone", "cybersecurity", "homeland_security"
    ],
    "Technology": [
        "technology", "internet", "data", "privacy", "algorithm", "platform",
        "social_media", "broadband", "telecom", "spectrum", "net_neutrality",
        "artificial_intelligence", "surveillance", "antitrust", "big_tech",
        "section_230", "digital", "software", "hardware", "semiconductor"
    ],
    "Real Estate": [
        "housing", "zoning", "mortgage", "rent", "landlord", "tenant",
        "construction", "development", "affordable_housing", "hud",
        "eviction", "property", "real_estate", "fannie", "freddie",
        "homeowner", "foreclosure", "building_code"
    ],
    "Agriculture": [
        "agriculture", "farm", "crop", "subsidy", "usda", "food",
        "pesticide", "herbicide", "gmo", "organic", "livestock", "dairy",
        "commodity", "grain", "ethanol", "rural", "irrigation", "conservation",
        "farm_bill", "crop_insurance"
    ],
    "Healthcare": [
        "healthcare", "hospital", "insurance", "medicaid", "medicare",
        "aca", "affordable_care", "obamacare", "provider", "physician",
        "nurse", "mental_health", "telehealth", "telemedicine", "hhs",
        "cms", "reimbursement", "public_option", "single_payer"
    ],
    "Tobacco & Alcohol": [
        "tobacco", "cigarette", "vaping", "alcohol", "spirits", "beer",
        "wine", "excise", "advertising", "marketing", "flavor", "menthol",
        "nicotine", "fda_tobacco"
    ],
    "Firearms": [
        "firearm", "gun", "weapon", "ammunition", "background_check",
        "second_amendment", "assault", "pistol", "rifle", "conceal",
        "carry", "nra", "atf", "registry", "silencer", "suppressor"
    ],
    "Insurance": [
        "insurance", "liability", "casualty", "underwriter", "premium",
        "deductible", "claim", "reinsurance", "actuary", "risk", "tort",
        "malpractice", "class_action", "damages", "punitive"
    ],
    "Telecommunications": [
        "telecom", "broadband", "spectrum", "wireless", "cable", "fiber",
        "fcc", "net_neutrality", "internet_access", "5g", "rural_broadband",
        "municipal_broadband", "right_of_way", "franchise"
    ],
    "Mining & Natural Resources": [
        "mining", "mineral", "extraction", "coal", "uranium", "rare_earth",
        "public_land", "blm", "national_forest", "royalty", "lease",
        "resource_extraction", "hardrock", "surface_mining"
    ],
    "Education": [
        "education", "school", "student", "teacher", "college", "university",
        "loan", "grant", "pell", "accreditation", "charter", "voucher",
        "curriculum", "common_core", "title_i", "department_of_education"
    ],
    "Labor & Unions": [
        "labor", "union", "wage", "worker", "employee", "collective_bargaining",
        "nlrb", "osha", "minimum_wage", "overtime", "right_to_work",
        "strike", "organizing", "contractor", "gig_economy"
    ],
}


def get_member_donor_industries(member_detail: dict) -> list:
    """
    Extract donor industry labels from a member's vault data.
    Falls back to inferring from top_donors string if structured data absent.
    Returns list of industry strings matching INDUSTRY_KEYWORDS keys.
    """
    industries = []

    # Direct field from fetch_finance.py (future structured data)
    if member_detail.get("top_donor_industries"):
        return member_detail["top_donor_industries"]

    # Infer from pac_industry or flags
    flags = member_detail.get("flags", [])
    for flag in flags:
        flag_lower = flag.lower()
        for industry in INDUSTRY_KEYWORDS:
            if any(kw in flag_lower for kw in industry.lower().split(" & ")):
                if industry not in industries:
                    industries.append(industry)

    # Fallback: use total_raised as proxy — if high PAC ratio, flag Finance
    pac = member_detail.get("pac_contributions", 0) or 0
    total = member_detail.get("total_raised", 0) or 0
    if total > 0 and pac / total > 0.4:
        if "Finance & Banking" not in industries:
            industries.append("Finance & Banking")

    return industries


def match_donor_interests(
    cleaned_text: str,
    bill_title: str,
    donor_industries: list,
) -> dict:
    """
    Check whether a bill's text/title overlaps with any donor industry keywords.

    Returns:
        {
            "match": True/False,
            "matched_industries": ["Oil & Gas", ...],
            "keyword_hits": {"Oil & Gas": ["pipeline", "emissions"], ...}
        }
    """
    if not donor_industries or not cleaned_text:
        return {"match": False, "matched_industries": [], "keyword_hits": {}}

    combined = (cleaned_text + " " + bill_title.lower()).lower()

    matched_industries = []
    keyword_hits = {}

    for industry in donor_industries:
        if industry not in INDUSTRY_KEYWORDS:
            continue
        keywords = INDUSTRY_KEYWORDS[industry]
        hits = [kw for kw in keywords if kw in combined]
        if hits:
            matched_industries.append(industry)
            keyword_hits[industry] = hits[:5]  # cap at 5 hits for storage

    return {
        "match": len(matched_industries) > 0,
        "matched_industries": matched_industries,
        "keyword_hits": keyword_hits,
    }


def score_donor_alignment(bill_results: list) -> float:
    """
    Given a list of bill analysis results for one member,
    return a 0–100 donor alignment score.
    Bills with ALEC match + donor interest match score highest.
    """
    if not bill_results:
        return 0.0

    total = 0.0
    for bill in bill_results:
        donor = bill.get("donor_interest", {})
        alec = bill.get("alec_match")
        if donor.get("match") and alec:
            total += 3.0  # strongest signal: ALEC template + donor aligned
        elif donor.get("match"):
            total += 1.5
        elif alec:
            total += 1.0

    # Normalize to 0–100 (cap at 100)
    raw = (total / len(bill_results)) * 25
    return min(round(raw, 1), 100.0)