# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CongressWatch is a public accountability tool tracking U.S. Congress members using government APIs. It generates an Anomaly Score (0-100) per member based on six weighted signals: stock trade timing (25%), wealth gap (25%), donor-vote alignment (20%), bill authorship/ALEC similarity (15%), foreign travel (10%), and attendance (5%).

Live site: congresswatch.vercel.app

## Architecture

**Frontend:** Single `index.html` file (plain HTML/CSS/JS, no framework). Canvas-drawn charts (no Chart.js).

**Data pipeline:** Nine scheduled workflows run daily via GitHub Actions on staggered schedules (UTC):
- `fetch.py` (1am) — Congress.gov member data (runs first, base data)
- `fetch_votes.py` (2am) — GovTrack vote history
- `fetch_finance.py` (5am) — FEC campaign finance + SEC EDGAR trade signals + anomaly scoring
- `run_fetch_bills.py` (7am) — Bill similarity engine (TF-IDF + cosine similarity)
- `fetch_senate_efd.py` (8am) — Senate stock trades from efdsearch.senate.gov PTR filings
- `fetch_travel_pdf.py` (9am) — House gift-travel filings from the Clerk's consolidated search (HTML, not PDFs — the disclosure PDFs are scanned images)
- `fetch_house_trades.py` (10am) — House stock trades: yearly FD.zip index from disclosures-clerk.house.gov + pdfplumber PTR PDF parsing
- `fetch_donors.py` (11am) — FEC Schedule A itemized donors (candidate → committee → contributions) + `top_donor_industries` derivation
- `fetch_senate_travel.py` (11:30am) — Senate Rule 35 gift-rule travel from the Secretary of the Senate's bulk XML (giftrule-disclosure.senate.gov; one GET, no auth). Member filings only; destination/sponsor/cost live in scanned PDFs, so records carry dates + document link. Rolling ~4-year source window — the script only ever ADDS trips
- `fetch_travel_costs.py` (1pm) — extracts trip costs from House gift-travel filing PDFs (Question 5 of the Sponsor Post-Travel Disclosure Form): pdfplumber text layer first (~14% of packets), tesseract OCR fallback for the rest (typed fills in a ruled table; toolchain is CI-only). `data/travel_cost_manifest.json` tracks per-doc outcomes so settled docs are never re-downloaded; 40 PDFs/run cap. `total_cost` = traveler row only; 0/empty means UNKNOWN, never "free trip"
- `finalize.yml` (12pm) — runs LAST: `recompute_scores.py` (authoritative rescore from whatever is on disk + promotion of detail-only fields like `alec_match_count`/`missed_votes_pct` to members.json) and `build_aggregates.py` (flattens the vault into `data/trades.json` + `data/bills.json`)

Each workflow commits updated JSON files back to the repo automatically (commit → `git pull --rebase` → push).

**Pipeline health rule: a green run proves nothing — check for the bot COMMIT.** Several fetchers can exit 0 with no data (site blocked, format changed). If a pipeline's daily commit is missing for more than a couple of days, inspect its run logs (`gh run list --workflow <file>.yml`).

**Data storage:** JSON files in `data/`. Two-tier structure:
- `data/members.json` — lightweight leaderboard (all 535+ members)
- `data/details/{bioguideId}.json` — full per-member vault (votes, finance, trades, bills, anomaly components)
- `data/bills/all_bills.json` — central bill cache with TF-IDF vectors (~100MB). NOT in the repo: it is gitignored and persists only via `actions/cache` in `fetch_bills.yml`. A cache eviction cold-starts the corpus, which legitimately disables ALEC matching for that run (`MIN_CORPUS_FOR_ALEC = 50`)
- `data/trades.json`, `data/bills.json`, `data/stats.json` — corpus-wide aggregates rebuilt daily by `finalize.yml`

## Bill Similarity Engine (fetch_bills/)

The most complex subsystem. Located in `fetch_bills/utils/`:
- `api_clients.py` — Congress.gov + LegiScan API wrappers with exponential backoff
- `text_processor.py` — Bill text cleaning (boilerplate removal, stopwords, bigrams)
- `similarity.py` — TF-IDF vectorizer (`max_features=8000`, `ngram_range=(1,2)`) + cosine similarity (threshold 0.80)
- `donor_matcher.py` — Maps bill text to donor industry categories (15 industries, 100+ keywords each)

Orchestrated by `run_fetch_bills.py` with config: `CURRENT_CONGRESS=119`, `MAX_BILLS_PER_MEMBER=10`, `LEGISCAN_QUERY_BUDGET=200`.

## Running Scripts Locally

Python 3.11. Install dependencies: `pip install -r requirements.txt`

Required environment variables (from GitHub Secrets):
- `CONGRESS_API_KEY` — Congress.gov API
- `FEC_API_KEY` — FEC.gov API
- `LEGISCAN_API_KEY` — LegiScan API

Run individual pipelines:
```bash
python fetch.py
python fetch_votes.py
python fetch_finance.py
python run_fetch_bills.py
```

Test scripts (manual, not pytest):
```bash
python fetch_finance_test.py    # Tests 5 members with TEST_MODE=True
python fetch_surgical_test.py   # Surgical test runs
```

## API Rate Limits

- Congress.gov: ~1 req/sec
- FEC.gov: ~1 req/sec
- LegiScan: ~2 sec/req
- SEC EDGAR: no key required, be respectful
- GovTrack: public, ~1 req/sec

All fetch scripts include built-in rate limiting (0.4-0.7s sleep between requests) and retry logic with exponential backoff on 429/5xx.

## Key Patterns

- Each pipeline uses safe merge logic — preserving existing fields when updating detail files
- All pipelines use atomic writes (temp file + `os.replace`) and guarded loads: a corrupt existing JSON file ABORTS the run rather than being treated as empty and overwritten
- Dates in trade/travel records are normalized to ISO `YYYY-MM-DD`; legacy records may still hold `MM/DD/YYYY`, so always parse before comparing
- SEC CIK resolution has a manual fallback mapping in `data/manual_cik_map.json`
- The ALEC corpus (`data/alec_corpus.json`) is matched against all bills via the same TF-IDF pipeline; ALEC matching is skipped when the bill corpus is cold (<50 bills)
- `vercel.json` serves `data/*` with CDN caching (5min browser / 1h CDN + stale-while-revalidate); everything else is `no-cache`
- `bootstrap.py` checks repo health and creates required data directories

## Legacy / unwired scripts (do not treat as live)

- `fetch_ptr.py` — old PTR PDF pipeline; no workflow invokes it and `data/ptr_source_manifest.json` is empty. Superseded by `fetch_house_trades.py`
- `fetch_trades_github_backup.py`, `fetch_travel_xml_backup.py` — dead predecessors kept for reference; both fully OVERWRITE `trades[]`/`travel[]` instead of safe-merging — do not resurrect as-is

## Changelog

At the end of every Claude Code session, create `changelog/YYYY-MM-DD-session-N.md` summarizing every file created or modified. This is required.
