"""
CongressWatch — Senate Stock Trade Fetcher
===========================================
Pulls Senate PTR (Periodic Transaction Report) trade data from the
pre-parsed senate-stock-watcher-data GitHub dataset.

Source: https://github.com/timothycarambat/senate-stock-watcher-data
No API key required — public dataset.

House PTR data: Not yet implemented. House disclosures require scraping
disclosures-clerk.house.gov/FinancialDisclosure (ASPX form) — planned
for a future update.

Supabase table schema — run in SQL Editor before first use:

-- CREATE TABLE IF NOT EXISTS public.trades (
--   id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
--   bioguide_id TEXT NOT NULL,
--   transaction_date DATE,
--   ticker TEXT,
--   asset_description TEXT,
--   asset_type TEXT,
--   trade_type TEXT,
--   amount_range TEXT,
--   owner TEXT,
--   ptr_link TEXT,
--   source TEXT DEFAULT 'senate_efd',
--   created_at TIMESTAMPTZ DEFAULT now(),
--   UNIQUE(bioguide_id, transaction_date, ticker, trade_type, amount_range)
-- );
-- CREATE INDEX IF NOT EXISTS idx_trades_member ON public.trades(bioguide_id);
-- CREATE INDEX IF NOT EXISTS idx_trades_date ON public.trades(transaction_date DESC);
-- CREATE INDEX IF NOT EXISTS idx_trades_ticker ON public.trades(ticker);
-- ALTER TABLE public.trades ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY "trades_read" ON public.trades FOR SELECT TO anon USING (true);
-- CREATE POLICY "trades_service_all" ON public.trades FOR ALL TO service_role USING (true) WITH CHECK (true);
"""

import os
import json
import requests
from datetime import datetime

SENATE_TRADES_URL = (
    "https://raw.githubusercontent.com/timothycarambat/"
    "senate-stock-watcher-data/master/data/aggregate/"
    "all_transactions_for_senators.json"
)

MEMBERS_FILE = "data/members.json"
DETAILS_DIR = "data/details"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

HEADERS = {
    "User-Agent": "CongressWatch/1.0 (public-interest-research)"
}

os.makedirs(DETAILS_DIR, exist_ok=True)


# ─── Helpers ────────────────────────────────────────────────

def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def normalize_name(name):
    """Lowercase, strip suffixes like Jr/Sr/III, non-alpha."""
    import re
    name = name.lower().strip()
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", name)
    name = re.sub(r"[^a-z\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


# ─── Supabase ───────────────────────────────────────────────

def supabase_upsert_trades(bid, trades, ptr_link=""):
    if not SUPABASE_URL or not SUPABASE_KEY or not trades:
        return
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    rows = []
    for t in trades:
        rows.append({
            "bioguide_id": bid,
            "transaction_date": t.get("transaction_date") or None,
            "ticker": t.get("ticker", ""),
            "asset_description": t.get("asset_description", ""),
            "asset_type": t.get("asset_type", ""),
            "trade_type": t.get("type", ""),
            "amount_range": t.get("amount", ""),
            "owner": t.get("owner", ""),
            "ptr_link": ptr_link,
            "source": "senate_efd"
        })
    success = 0
    for i in range(0, len(rows), 100):
        chunk = rows[i:i+100]
        try:
            r = requests.post(
                f"{SUPABASE_URL}/rest/v1/trades",
                headers=headers,
                json=chunk,
                timeout=30
            )
            if r.status_code in (200, 201):
                success += len(chunk)
            else:
                print(f"    Supabase trades batch {i}: {r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"    Supabase trades error: {e}")
    return success


# ─── Main ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("CongressWatch — Senate Stock Trade Fetcher")
    print("=" * 60)

    # Load members
    members = load_json(MEMBERS_FILE, [])
    if not members:
        print("ERROR: data/members.json not found or empty")
        exit(1)

    senators = [m for m in members if (m.get("chamber", "") or "").lower() == "senate"]
    print(f"Loaded {len(members)} members, {len(senators)} senators")

    # Download Senate trade data
    print(f"\nFetching Senate trade data from GitHub...")
    try:
        r = requests.get(SENATE_TRADES_URL, headers=HEADERS, timeout=60)
        r.raise_for_status()
        senate_data = r.json()
        print(f"Downloaded {len(senate_data)} senator trade records")
    except Exception as e:
        print(f"ERROR: Could not fetch Senate trade data: {e}")
        exit(1)

    # Build lookup by normalized last name
    trade_lookup = {}
    for entry in senate_data:
        last = normalize_name(entry.get("last_name", ""))
        if last:
            # Multiple senators may share a last name — collect all
            if last not in trade_lookup:
                trade_lookup[last] = []
            trade_lookup[last].append(entry)

    # Match senators
    matched = 0
    total_trades = 0
    supabase_total = 0

    for m in members:
        bid = m.get("id") or m.get("bioguide_id", "")
        if not bid:
            continue
        if (m.get("chamber", "") or "").lower() != "senate":
            continue

        name = m.get("name", "")
        # Extract last name from "First Last" or "First Middle Last"
        parts = name.strip().split()
        last = normalize_name(parts[-1]) if parts else ""

        candidates = trade_lookup.get(last, [])

        # If multiple candidates, try to match first name too
        best = None
        if len(candidates) == 1:
            best = candidates[0]
        elif len(candidates) > 1:
            first = normalize_name(parts[0]) if parts else ""
            for c in candidates:
                c_first = normalize_name(c.get("first_name", ""))
                if c_first and first and c_first.startswith(first[:3]):
                    best = c
                    break
            if not best:
                best = candidates[0]  # fallback to first match

        if not best:
            continue

        transactions = best.get("transactions", [])
        if not transactions:
            continue

        matched += 1
        total_trades += len(transactions)
        ptr_link = best.get("ptr_link", "")

        # Find latest trade date
        dates = [t.get("transaction_date", "") for t in transactions if t.get("transaction_date")]
        latest_date = max(dates) if dates else ""

        # Safe merge into detail file
        detail = load_json(os.path.join(DETAILS_DIR, f"{bid}.json"), {})
        detail["trades"] = transactions
        detail["trades_updated"] = datetime.now().isoformat()
        detail["trade_count"] = len(transactions)
        detail["latest_trade_date"] = latest_date
        save_json(os.path.join(DETAILS_DIR, f"{bid}.json"), detail)

        # Update members.json entry
        m["trade_count"] = len(transactions)
        m["latest_trade_date"] = latest_date

        # Supabase
        try:
            count = supabase_upsert_trades(bid, transactions, ptr_link)
            if count:
                supabase_total += count
        except Exception as e:
            print(f"  Supabase error for {bid}: {e}")

        print(f"  {name}: {len(transactions)} trades (latest: {latest_date})")

    # Write updated members.json
    save_json(MEMBERS_FILE, members)

    # Summary
    print(f"\n{'=' * 60}")
    print(f"SENATE TRADE FETCH COMPLETE")
    print(f"{'=' * 60}")
    print(f"Senators matched:  {matched}/{len(senators)}")
    print(f"Total trades:      {total_trades}")
    if SUPABASE_URL:
        print(f"Supabase upserted: {supabase_total}")
    print(f"{'=' * 60}")
