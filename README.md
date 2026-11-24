CongressWatch 🔍
"V1 is now V3, R.I.P v2"
Public records. Public accountability.
A free, open-source public accountability tool that tracks U.S. Congress members using 100% public government records. No ads. No sponsors. No political agenda. Just data.
🌐 Live site: www.congresswatch.us
🐦 Follow updates: [@opnsorcepatents](https://x.com/opnsorcepatents)

What Is This?
CongressWatch cross-references public records from multiple government databases to generate an Anomaly Score (0–100) for every sitting member of Congress. The score is a statistical indicator — not a legal judgment — that highlights patterns worth public attention.
Higher scores reflect more of the implemented signals in the available records. Low scores may reflect missing or stale data, not a clean bill of health. Review source filings and coverage before drawing conclusions.

Data Sources
The active pipelines use public government disclosures, government APIs, GovTrack, and official GovInfo bulk XML. Availability and source coverage vary. A missing record is not evidence of no activity.



|Source                                     |What We Pull                                        |
|-------------------------------------------|----------------------------------------------------|
|**[Congress.gov](http://Congress.gov) API**|Member bios, committee assignments, official records|
|**[FEC.gov](http://FEC.gov) API**          |Campaign finance, PAC donations, individual donors  |
|**SEC EDGAR**                              |Corporate-insider Form 4 filings (not STOCK Act PTRs)                |
|**Senate eFD**                             |Senate financial disclosure reports                 |
|**House eFD**                              |House financial disclosure reports                  |
|**GovInfo official bulk XML**              |Bill Status metadata and versioned legislative prose |
|**[GovTrack.us](http://GovTrack.us)**      |Voting history, ideology scores                     |

|**BioGuide (Congress)**                    |Official member photos                              |

How The Anomaly Score Works
The score retains a 0–100 scale with six component slots. The personal-wealth component is disabled (0 points) until sourced personal financial data exists; the other components are not rescaled. Campaign fundraising is not personal wealth. These are heuristic activity indicators, not a validated probability of misconduct:



|Signal              |Weight|What It Measures                                   |
|--------------------|------|---------------------------------------------------|
|Disclosed trade activity|25%   |Recent disclosed-trade counts and SEC signals       |
|Personal wealth     |25%   |Unavailable: no sourced personal wealth data      |
|Bill/donor overlap|20%   |Unavailable; unsupported legacy method suspended |
|Model-bill similarity|15%   |Unavailable; no verified primary model corpus |
|Disclosed travel    |10%   |Recorded gift trips, including domestic travel      |
|Attendance          |5%    |Missed votes within the available sample          |

Attendance methodology: scored over the most recent 100 roll-call votes per
member (GovTrack `vote_voter`, `fetch_votes.VOTE_WINDOW`). A "missed" vote is a
position of Not Voting, Absent, or blank. The penalty scales linearly with the
missed-vote percentage and saturates at 25% missed, where the full 5 points
apply (`ATTENDANCE_SATURATION_PCT` in `fetch_finance.py`) — so 5% missed costs
1 point, 10% costs 2, and anything at or above 25% costs 5. Members with fewer
than 20 votes on record score 0 for this signal rather than being penalized on
an unreliable sample.

Non-voting delegates are exempt. The five House delegates (District of Columbia,
Virgin Islands, Guam, American Samoa, Northern Mariana Islands) and Puerto
Rico's Resident Commissioner cannot vote on final passage, so a "missed vote" is
not a meaningful measure of their attendance. They score 0 for this signal and
their profiles display "N/A — non-voting delegate" instead of a percentage. The
member schema has no role field, so they are identified by exact state +
chamber match (`NON_VOTING_STATES` in `fetch_finance.py`) — never a substring
test, since "Virgin Islands" shares a prefix with "Virginia" and "West
Virginia". `test_delegates.py` asserts this and is worth running after any
change to that classifier.

The member profile separately flags absence above 15% as
a warning; that threshold is deliberately a tier below the scoring saturation
point, not the same number.

Important: The anomaly score is a statistical indicator only. It does not imply illegal activity or wrongdoing. All inputs are from public records. Full methodology is open source and auditable in this repository.

SEC identity, source retrieval and campaign finances
SEC company/name-search results are candidate observations, not verified congressional activity. A company CIK, a name match, or the old `verified_auto` resolver label is never enough. Raw CIKs, counts, source failures and historical observations remain available for audit; they do not independently create points, activity flags or attributed SEC totals.

An SEC count becomes eligible only when all of these checks pass:
- An explicit reviewed `identity_status: verified` entry supplies a CIK and HTTPS evidence in `data/manual_cik_map.json`.
- `edgar_identity_cik` exactly matches the CIK associated with the count in `edgar_cik`.
- Every declared evidence URL is retrievable over HTTPS. The finance collector records a receipt containing the exact URL, resolved HTTPS URL, CIK, UTC check time, response byte count and SHA-256 of the nonempty response. Redirects are bounded; non-200, empty, oversized or failed responses remain unavailable. A 403 is not bypassed or retried by this evidence fetch.
- Receipts in `edgar_identity_evidence_checks` match the evidence list and CIK. Successful retrieval establishes availability only: the separate manual identity review remains necessary.
- SEC data retrieval succeeded (`edgar_data_status: available`, not `query_failed`) and its count is finite, numeric and nonnegative.

Missing or failed evidence excludes the count from scoring and SEC-derived flags. A failed refresh retains paired raw CIK/count provenance as stale evidence; it does not make the retained count current. A failed manual evidence fetch is marked `verification_unavailable`, while the original reviewed declaration is retained in `edgar_identity_assertion`. The frontend uses the same receipt/identity gate. Evidence links alone are not proof that a member owns a company or performed a trade.

The SEC and campaign-finance paths are independent in `fetch_finance.py` and the finance Actions workflow: EDGAR supplies raw filing candidates; FEC supplies campaign fundraising and contribution fields. Campaign receipts, PAC/committee contributions and individual contributions remain visible as separate campaign facts. They never become personal net worth, never enter the salary estimate and never increase the wealth component.

The compatibility key `wealth_gap` remains numeric zero with its original 25-point maximum. Zero here means **suspended/unavailable**, not zero wealth and not a verified absence of a wealth discrepancy. Other components are not reweighted to fill missing points. The base-pay schedule and estimated cumulative congressional salary remain separate; that estimate is not actual take-home pay, personal assets or net worth. Unsourced personal net worth displays unavailable.

The Bill Similarity Engine
The replacement engine joins official GovInfo Bill Status sponsor Bioguide IDs to official legislative XML across all 24 119th-Congress bulk partitions. It extracts legislative prose, excludes deleted text, tables of contents and unconsolidated amendment instructions, and records source/version URLs and raw/text SHA-256 hashes. It prefers enrolled full text, otherwise the newest dated complete body with a deterministic tie-break.

Bounded TF-IDF candidates use cosine >= 0.30, with at most 40 candidates per bill. A published comparison additionally requires at least 32 contiguous normalized words and 20% distinct 12-word-shingle coverage on at least one side. Up to three qualifying matches retain the exact excerpts. These thresholds define shared wording, not a copying percentage, policy equivalence, authorship, lobbying influence or wrongdoing.

Donor alignment and ALEC/model matching are unavailable: the current corpus has neither a validated donor method nor a verified primary model corpus. Their legacy component slots remain zero without reweighting. Old polluted PDF-derived claims and manual history remain archive-only. Bills whose official text has not been published are visible with that coverage status, not a successful non-match.

The scheduled bill job builds in an isolated snapshot and audits all members, source partitions, quoted passages and aggregate identities before installation. Missing/shrinking source coverage, source errors or a moving main head prevent publication and retain the last good snapshot. Bill refresh and Finalize share a writer lock. The reviewed 119th-only pipeline stops at January 1, 2027 until the Congress transition receives explicit review. `review-validation.yml` exercises the same generator with read-only repository permissions and never installs, pushes, merges or deploys.

Current coverage and downstream compatibility
Coverage is reported explicitly in `audit_data.py`, the member panels and the source manifests. The Nov. 24 source ingest contains 20,197 official metadata identities and 23,533 full-text versions across 20,035 bills, with zero ingestion errors. This is a dated snapshot, not a promise of complete future coverage. The stored vote refresh still reports failure for all 539 current members. Scanned trade placeholders and unknown travel costs remain visible; blank fields do not prove no activity or a free trip. Staff/unverified travel is retained as raw provenance but excluded from the member travel score.

OSP-API remains a separate, unmodified repository. Its immutable draft `bc828aefa681b043c63a05fb1c7530ed2332d303` is tested against these files through actual route/scoring code in an isolated harness, with auth/HTTP transport stubbed and no database access. Routes, envelopes, field types and missing/unavailable input behavior are checked. The observed API differences remain release blockers: staff travel is still scored there, and its SEC guard does not require evidence retrieval receipts. API alignment and Charlie's manual review must precede coordinated rollout.

Local verification
Use Python 3.12 and Node 24. Install `requirements-official-prose.txt`, the legacy collector test dependencies (`beautifulsoup4 pdfplumber pypdf`), `ruff==0.14.6`, and Playwright; run `npm ci`. Then run `npm run lint`, `npm run build`, the offline unittest modules listed in `review-validation.yml`, `python test_delegates.py`, `python audit_data.py`, `python validation/verify_generation.py`, and `python validation/browser_review.py`. API checks fetch exact Git blobs using `validation/fetch_api_reference.py`, then execute `node validation/check_api_reference.mjs`; the report explicitly lists parity blockers.

How To Save As An App On Your Phone
CongressWatch is a web app, which means you can add it to your home screen and it works exactly like a native app — no App Store required.
iPhone (Safari):
	1.	Go to www.congresswatch.us in Safari
	2.	Tap the Share button (box with arrow pointing up)
	3.	Scroll down and tap “Add to Home Screen”
	4.	Tap “Add”
	5.	CongressWatch now appears on your home screen like any app
Android (Chrome):
	1.	Go to www.congresswatch.us in Chrome
	2.	Tap the three dots menu (top right)
	3.	Tap “Add to Home Screen”
	4.	Tap “Add”

How It’s Built
	∙	Frontend: Plain HTML/CSS/JS — no frameworks, loads instantly
	∙	Data pipeline: Python, runs automatically every day via GitHub Actions
	∙	Hosting: Vercel (free)
	∙	Database: GitHub repo (JSON files served from the Vercel CDN)
	∙	Public API: the published JSON is consumed by the OSP-API at api.opensourceforall.com
	∙	Cost to run: $0

Roadmap / Coming Soon
	∙	Full member profile pages — dedicated page per member with complete trade history, donor breakdown, bill list, vote record timeline
	∙	Live stock trade alerts — push notifications when a member makes a trade
	∙	Donor network graph — visual map of who funds who
	∙	Bill similarity explorer — browse clusters of similar bill text
	∙	Compare mode — side by side comparison of any two members
	∙	Leaderboards — most trades, biggest wealth gap, most missed votes
	∙	Download raw data — CSV/JSON export of everything
	∙	API access — for journalists and researchers
	∙	iOS/Android app — native app with push notifications

Philosophy
This project exists because sunlight is the best disinfectant. Every number on this site comes from records that are technically public — but buried across dozens of government databases that most people don’t know exist and can’t easily search.
CongressWatch makes that data human-readable.
We are not affiliated with any political party, PAC, news organization, or government agency. We accept no advertising or sponsored content. If you find an error, open a GitHub issue and we’ll correct it within 48 hours.

Contributing
This is open source. Pull requests welcome. If you’re a journalist, researcher, or developer who wants API access or wants to collaborate, reach out on X: @opensourcepatents

Legal
All data sourced from public government records under open government principles. Current bill analysis uses official federal legislative XML from GovInfo; legacy source records retain their original provenance. This tool is for public interest research and journalism.
CongressWatch is not a lawyer and this is not legal advice. Anomaly scores are statistical indicators only.
