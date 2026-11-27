CongressWatch 🔍
"V1 is now V3, R.I.P v2"
535 Members. Zero Accountability.
A free, open-source public accountability tool that tracks U.S. Congress members using 100% public government records. No ads. No sponsors. No political agenda. Just data.
🌐 Live site: congresswatch.vercel.app
🐦 Follow updates: [@opnsorcepatents](https://x.com/opnsorcepatents)

What Is This?
CongressWatch cross-references public records from multiple government databases to generate an Anomaly Score (0–100) for every sitting member of Congress. The score is a statistical indicator — not a legal judgment — that highlights patterns worth public attention.
High scores mean the data shows unusual patterns. Low scores mean the data looks typical. Every number links to its source.

Data Sources
All data is 100% public record, legally obtained via official government APIs.



|Source                                     |What We Pull                                        |
|-------------------------------------------|----------------------------------------------------|
|**[Congress.gov](http://Congress.gov) API**|Member bios, committee assignments, official records|
|**[FEC.gov](http://FEC.gov) API**          |Campaign finance, PAC donations, individual donors  |
|**SEC EDGAR**                              |STOCK Act trade disclosures (Form 4)                |
|**Senate eFD**                             |Senate financial disclosure reports                 |
|**House eFD**                              |House financial disclosure reports                  |
|**LegiScan API**                           |Full bill text for NLP similarity analysis          |
|**[GovTrack.us](http://GovTrack.us)**      |Voting history, ideology scores                     |
|**OpenSecrets**                            |Career finance totals, industry breakdowns          |
|**BioGuide (Congress)**                    |Official member photos                              |

How The Anomaly Score Works
Each member receives a score from 0–100 based on six weighted signals:



|Signal              |Weight|What It Measures                                   |
|--------------------|------|---------------------------------------------------|
|Stock trade timing  |25%   |Trades within 30 days of related legislation       |
|Wealth gap          |25%   |Net worth vs. cumulative congressional salary      |
|Donor-vote alignment|20%   |Does their voting record match donor interests?    |
|Bill authorship     |15%   |Are their bills copied from lobbying org templates?|
|Foreign travel      |10%   |Trips sponsored by foreign-connected entities      |
|Attendance          |5%    |Missed votes while collecting full salary          |

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

The Bill Similarity Engine
The most unique feature of CongressWatch is the NLP bill similarity engine. Using TF-IDF vectorization and cosine similarity, it compares the text of every bill in Congress to detect when “independently authored” bills share suspiciously similar language — revealing coordinated third-party authorship from lobbying organizations like ALEC, PhRMA, and others.
This feature does not exist in any other public tool.

How To Save As An App On Your Phone
CongressWatch is a web app, which means you can add it to your home screen and it works exactly like a native app — no App Store required.
iPhone (Safari):
	1.	Go to congresswatch.vercel.app in Safari
	2.	Tap the Share button (box with arrow pointing up)
	3.	Scroll down and tap “Add to Home Screen”
	4.	Tap “Add”
	5.	CongressWatch now appears on your home screen like any app
Android (Chrome):
	1.	Go to congresswatch.vercel.app in Chrome
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
	∙	Bill similarity explorer — browse clusters of ghost-written legislation
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
All data sourced from public government records under open government principles. Bill text data licensed under CC BY 4.0 via LegiScan. This tool is for public interest research and journalism.
CongressWatch is not a lawyer and this is not legal advice. Anomaly scores are statistical indicators only.

## SEC identity and wrong-owner protection

SEC name searches produce **candidate observations**, not verified congressional
identities. Raw `corporate_insider_signals`, CIKs and retrieval provenance remain
available in the JSON. They affect the SEC portion of scores, the `trade` flag,
card badges, totals, and Overview/Stocks/Patterns warnings only when all of these
requirements hold:

- An explicitly reviewed `data/manual_cik_map.json` entry has
  `identity_status: "verified"`, the member's exact `bioguide_id`, a ten-digit
  zero-padded `cik`, and nonempty HTTPS `evidence` URLs. The map key and declared
  member must agree. Name frequency and legacy `verified_auto`/bare manual CIKs
  are insufficient.
- The stored verified member ID and identity CIK match the member and SEC CIK
  exactly. Every declared URL has exactly one ordered, available retrieval
  receipt bound to that original URL and CIK, with its HTTPS resolved URL,
  retrieval timestamp and SHA-256. Duplicate URLs, missing/reordered receipts,
  unavailable evidence, HTTP redirects and empty evidence fail closed. Successful
  retrieval alone does not establish identity: the explicit review is separate.
- The SEC count fetch succeeded for that CIK (`edgar_data_status: "available"`
  and `edgar_status: "verified_manual"`). Counts must be nonnegative integers.
  Failed/partial searches retain the old raw CIK/count pair and last-good time,
  advance the attempt timestamp, and contribute no SEC points or activity flags.

`python validation/scrub_sec_outputs.py` applies the focused offline correction
across member details, roster and stats. It preserves every raw congressional
trade row, finance/vote/travel/bill input, and every non-SEC score component and
flag. Genuine recent PTR trades still contribute their existing frequency points.
It marks regenerated records with `sec_attribution_version: 1`; the UI treats
older cached wrong-owner scores as pending until corrected data arrive. The
normal finance fetcher and finalizer apply the same SEC gate on subsequent runs.
No routes or existing raw field types are changed; verification metadata and
roster score components are additive. OSP-API is not changed by this focused fix.

This correction deliberately does not change the existing wealth, donor, bill,
travel or attendance methodologies. Those broader concerns remain separate work
(including #21 and draft #20); campaign receipts must not be interpreted as a
personal financial disclosure. A positive SEC fixture tests the gate, not an
assertion that any current member has verified SEC ownership.

Offline review: `npm ci`, `pip install requests ruff playwright`, `npm run lint`,
`npm run build`, `python -m unittest test_sec_identity -v`,
`python validation/audit_sec_scope.py`, and `python validation/browser_sec.py`
(after installing Playwright Chromium). The review workflow is read-only and
publishes no site or data. The focused review branch skips Vercel builds. No
production source workflow is required to validate this change.


### Reviewed finance snapshot reconciliation (27 November 2026)

The focused SEC review retains the complete incoming and prior raw CIK/count/status
observations in `validation/finance_run_46018133463.json`, with member-bound job
log excerpts and the original job-log hash. `validation/reconcile_finance.py`
replays the reviewed failure rules offline: any failed alias or count query keeps
the prior detail CIK/count together and marks the attempt stale or unavailable.
A partial search's zero is not a clean result. Successful automatic candidates
remain unverified and cannot contribute SEC scores, flags or warnings.

The incoming nine-filing CIK for Rep. Patrick Ryan was returned after a failed
alias query. The official submissions response identifies `0001897386` as
`Thompson Patrick Ryan`, a different filer; its URL and response hash are retained.
The rejected observation remains inspectable, while the prior raw pair is kept.
Neither pair establishes congressional identity. No last-good timestamp is
invented from buffered job logs or upstream processing timestamps. All 539
effective scores and flags match the previous reviewed snapshot; genuine trade
rows and other components are unchanged. Production ingestion logic is unchanged
by this reconciliation; it already contains the reviewed fail-closed rules.
