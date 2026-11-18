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
The active pipelines use public government disclosures, government APIs, GovTrack, and LegiScan. Availability and source coverage vary. A missing record is not evidence of no activity.



|Source                                     |What We Pull                                        |
|-------------------------------------------|----------------------------------------------------|
|**[Congress.gov](http://Congress.gov) API**|Member bios, committee assignments, official records|
|**[FEC.gov](http://FEC.gov) API**          |Campaign finance, PAC donations, individual donors  |
|**SEC EDGAR**                              |Corporate-insider Form 4 filings (not STOCK Act PTRs)                |
|**Senate eFD**                             |Senate financial disclosure reports                 |
|**House eFD**                              |House financial disclosure reports                  |
|**LegiScan API**                           |Full bill text for NLP similarity analysis          |
|**[GovTrack.us](http://GovTrack.us)**      |Voting history, ideology scores                     |

|**BioGuide (Congress)**                    |Official member photos                              |

How The Anomaly Score Works
The score retains a 0–100 scale with six component slots. The personal-wealth component is disabled (0 points) until sourced personal financial data exists; the other components are not rescaled. Campaign fundraising is not personal wealth. These are heuristic activity indicators, not a validated probability of misconduct:



|Signal              |Weight|What It Measures                                   |
|--------------------|------|---------------------------------------------------|
|Disclosed trade activity|25%   |Recent disclosed-trade counts and SEC signals       |
|Personal wealth     |25%   |Unavailable: no sourced personal wealth data      |
|Bill/donor overlap|20%   |Bill-language overlap with donor industry keywords    |
|Model-bill similarity|15%   |TF-IDF text overlap with model templates|
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

The Bill Similarity Engine
The bill pipeline compares fetched bill text using TF-IDF vectorization and cosine similarity. It reports related bills and overlap with the stored model-legislation corpus at an 0.80 match threshold. Coverage is limited to fetched bills, available full text, and the current template corpus, not every bill in Congress.

Text similarity does not establish who wrote a bill, coordination, lobbying influence, copying, or wrongdoing. Shared language can reflect common legal provisions or related proposals. Matches are research leads requiring independent source review. Keyword overlap with donor industries is not evidence of how a member voted or why.

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
All data sourced from public government records under open government principles. Bill text data licensed under CC BY 4.0 via LegiScan. This tool is for public interest research and journalism.
CongressWatch is not a lawyer and this is not legal advice. Anomaly scores are statistical indicators only.
