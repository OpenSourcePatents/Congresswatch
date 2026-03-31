# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CongressWatch is a public accountability tool tracking U.S. Congress members using government APIs. It generates an Anomaly Score (0-100) per member based on six weighted signals: stock trade timing (25%), wealth gap (25%), donor-vote alignment (20%), bill authorship/ALEC similarity (15%), foreign travel (10%), and attendance (5%).

Live site: congresswatch.vercel.app

## Architecture

**Frontend:** Single `index.html` file (plain HTML/CSS/JS, no framework). Canvas-drawn charts (no Chart.js).

**Data pipeline:** Four independent Python scripts run daily via GitHub Actions on staggered schedules (UTC):
- `fetch.py` (1am) — Congress.gov member data (runs first, base data)
- `fetch_votes.py` (2am) — GovTrack vote history
- `fetch_finance.py` (4am) — FEC campaign finance + SEC EDGAR trade signals + scoring
- `run_fetch_bills.py` (6am) — Bill similarity engine (TF-IDF + cosine similarity)

Each workflow commits updated JSON files back to the repo automatically.

**Data storage:** JSON files in `data/`. Two-tier structure:
- `data/members.json` — lightweight leaderboard (all 535+ members)
- `data/details/{bioguideId}.json` — full per-member vault (votes, finance, trades, bills, anomaly components)
- `data/bills/all_bills.json` — central bill cache with TF-IDF vectors (~104MB)

## Bill Similarity Engine (fetch_bills/)

The most complex subsystem. Located in `fetch_bills/utils/`:
- `api_clients.py` — Congress.gov + LegiScan API wrappers with exponential backoff
- `text_processor.py` — Bill text cleaning (boilerplate removal, stopwords, bigrams)
- `similarity.py` — TF-IDF vectorizer (`max_features=8000`, `ngram_range=(1,2)`) + cosine similarity (threshold 0.80)
- `donor_matcher.py` — Maps bill text to donor industry categories (15 industries, 100+ keywords each)

Orchestrated by `run_fetch_bills.py` with config: `CURRENT_CONGRESS=119`, `MAX_BILLS_PER_MEMBER=10`, `LEGISCAN_QUERY_BUDGET=200`.

## Running Scripts Locally

Python 3.11. Install dependencies: `pip install requests scikit-learn numpy beautifulsoup4 pypdf`

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
- SEC CIK resolution has a manual fallback mapping in `data/manual_cik_map.json`
- PDF parsing (PTR reports in `fetch_ptr.py`) tries `pypdf` first, falls back to `PyPDF2`
- The ALEC corpus (`data/alec_corpus.json`) is matched against all bills via the same TF-IDF pipeline
- `vercel.json` disables all caching (no-cache headers on every response)
- PTR pipeline (`fetch_ptr.py`) requires `data/ptr_source_manifest.json` with source URLs
- `bootstrap.py` checks repo health and creates required data directories

## Changelog

At the end of every Claude Code session, create `changelog/YYYY-MM-DD-session-N.md` summarizing every file created or modified. This is required.
