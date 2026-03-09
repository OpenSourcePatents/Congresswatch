"""
api_clients.py — Congress.gov + LegiScan wrappers with throttling and retry logic.
"""

import os
import time
import random
import requests
import base64

CONGRESS_API_KEY = os.environ.get("CONGRESS_API_KEY", "")
LEGISCAN_API_KEY = os.environ.get("LEGISCAN_API_KEY", "")

CONGRESS_BASE = "https://api.congress.gov/v3"
LEGISCAN_BASE = "https://api.legiscan.com/"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get(url, params=None, retries=3, base_delay=2.0, label=""):
    """GET with exponential backoff on 429/5xx."""
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code == 200:
                return r
            if r.status_code == 429:
                wait = (2 ** attempt) * base_delay + random.uniform(0, 1)
                print(f"    [429] Rate limited {label}. Waiting {wait:.1f}s...")
                time.sleep(wait)
                continue
            if r.status_code in (500, 502, 503, 504):
                wait = (2 ** attempt) * base_delay
                print(f"    [{r.status_code}] Server error {label}. Waiting {wait:.1f}s...")
                time.sleep(wait)
                continue
            print(f"    [HTTP {r.status_code}] {label} — skipping")
            return None
        except requests.exceptions.RequestException as e:
            wait = (2 ** attempt) * base_delay
            print(f"    [ERR] {label}: {e}. Waiting {wait:.1f}s...")
            time.sleep(wait)
    return None


def _sleep():
    time.sleep(0.4 + random.uniform(0, 0.3))


# ---------------------------------------------------------------------------
# Congress.gov
# ---------------------------------------------------------------------------

def congress_get(path, params=None):
    """Make a Congress.gov API call."""
    p = {"api_key": CONGRESS_API_KEY, "format": "json", "limit": 250}
    if params:
        p.update(params)
    url = f"{CONGRESS_BASE}/{path.lstrip('/')}"
    r = _get(url, params=p, label=path)
    if r is None:
        return None
    try:
        return r.json()
    except Exception as e:
        print(f"    [JSON ERR] Congress.gov {path}: {e}")
        return None


def get_member_sponsored_bills(bioguide_id, congress=119, limit=20):
    """
    Fetch bills sponsored by a member in the given congress.
    Returns list of bill stubs: {bill_id, title, type, number, congress, url}
    """
    data = congress_get(
        f"member/{bioguide_id}/sponsored-legislation",
        {"congress": congress, "limit": limit}
    )
    if not data:
        return []

    bills = []
    for item in data.get("sponsoredLegislation", []):
        bill_type = item.get("type", "").upper()
        number = item.get("number", "")
        cong = item.get("congress", congress)
        bill_id = f"{bill_type}{number}-{cong}"
        bills.append({
            "bill_id":  bill_id,
            "title":    item.get("title", ""),
            "type":     bill_type,
            "number":   str(number),
            "congress": cong,
            "url":      item.get("url", ""),
            "introduced_date": item.get("introducedDate", ""),
            "latest_action": item.get("latestAction", {}).get("text", ""),
        })
    _sleep()
    return bills


def get_bill_cosponsors(congress, bill_type, number):
    """
    Returns list of bioguide IDs of co-sponsors for a given bill.
    """
    data = congress_get(f"bill/{congress}/{bill_type.lower()}/{number}/cosponsors")
    if not data:
        return []
    ids = []
    for cs in data.get("cosponsors", []):
        bid = cs.get("bioguideId", "")
        if bid:
            ids.append(bid)
    _sleep()
    return ids


def get_congress_bill_text_url(congress, bill_type, number):
    """
    Returns a URL to the bill text from Congress.gov if available.
    """
    data = congress_get(f"bill/{congress}/{bill_type.lower()}/{number}/text")
    if not data:
        return None
    formats = data.get("textVersions", [])
    for version in formats:
        for fmt in version.get("formats", []):
            if fmt.get("type", "").lower() in ("formatted text", "plain text", "txt"):
                return fmt.get("url")
    _sleep()
    return None


def fetch_congress_text(url):
    """Fetch raw bill text from a Congress.gov text URL."""
    if not url:
        return ""
    r = _get(url, label="congress text")
    if r is None:
        return ""
    _sleep()
    return r.text[:50000]  # cap at 50k chars


# ---------------------------------------------------------------------------
# LegiScan
# ---------------------------------------------------------------------------

def legiscan_get(op, params=None):
    """Make a LegiScan API call."""
    p = {"key": LEGISCAN_API_KEY, "op": op}
    if params:
        p.update(params)
    r = _get(LEGISCAN_BASE, params=p, label=f"LegiScan:{op}")
    if r is None:
        return None
    try:
        data = r.json()
        if data.get("status") == "OK":
            return data
        print(f"    [LegiScan] Non-OK status for {op}: {data.get('status')}")
        return None
    except Exception as e:
        print(f"    [JSON ERR] LegiScan {op}: {e}")
        return None


def legiscan_search_bill(query, state="US", year=2):
    """
    Search LegiScan for bills matching query.
    year=2 = current sessions. Returns list of result stubs.
    """
    data = legiscan_get("getSearch", {"query": query, "state": state, "year": year})
    if not data:
        return []
    results = data.get("searchresult", {})
    bills = []
    for key, val in results.items():
        if key == "summary":
            continue
        if isinstance(val, dict):
            bills.append({
                "legiscan_id": val.get("bill_id"),
                "bill_number": val.get("bill_number", ""),
                "title":       val.get("title", ""),
                "state":       val.get("state", ""),
                "last_action": val.get("last_action", ""),
                "change_hash": val.get("change_hash", ""),
            })
    _sleep()
    return bills


def legiscan_get_bill(bill_id):
    """Get full bill details from LegiScan including text documents list."""
    data = legiscan_get("getBill", {"id": bill_id})
    if not data:
        return None
    _sleep()
    return data.get("bill", {})


def legiscan_get_bill_text(doc_id):
    """
    Fetch bill text document from LegiScan (base64 encoded).
    Returns decoded plain text string or empty string.
    """
    data = legiscan_get("getBillText", {"id": doc_id})
    if not data:
        return ""
    doc = data.get("text", {})
    encoded = doc.get("doc", "")
    if not encoded:
        return ""
    try:
        decoded = base64.b64decode(encoded).decode("utf-8", errors="replace")
        _sleep()
        return decoded[:50000]  # cap at 50k chars
    except Exception as e:
        print(f"    [B64 ERR] doc_id={doc_id}: {e}")
        return ""


def legiscan_get_text_for_bill(bill_id):
    """
    Convenience: get the most recent bill text for a LegiScan bill_id.
    Returns plain text string or empty string.
    """
    bill = legiscan_get_bill(bill_id)
    if not bill:
        return ""
    texts = bill.get("texts", [])
    if not texts:
        return ""
    # Most recent first (highest doc_id)
    texts_sorted = sorted(texts, key=lambda t: t.get("doc_id", 0), reverse=True)
    return legiscan_get_bill_text(texts_sorted[0]["doc_id"])