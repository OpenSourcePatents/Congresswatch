CongressWatch 🔍
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
	∙	Database: GitHub repo (JSON files, upgrading to Supabase)
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