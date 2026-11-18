# Main reconciliation for draft PR #20

Pinned main:506ac59adf260d987646832cdcff7d8a9b762225.
Prior review:04a1cb005e68f5a5e9ffeb493d8f97ebc1c1db04.

Main changed only data since the shared ancestor4e506f24. Review source code and
all integrity fixes are retained. A two-parent review commit records main
ancestry without modifying main or deploying.

Every latest bill record is verified against its archived original fields in
review, preserving order and malformed IDs. Trade, travel, vote and donor arrays
match pinned main exactly; quarantined bill findings stay suspended. Remaining
source deltas are incorporated: two FEC contribution updates and successful raw
SEC candidate counts, including Mary Miller103→104. These counts are NOT
verified member activity. All current identities remain unverified and excluded
from scoring. Scores are unchanged by this reconciliation.

Finance run268 logged failures affecting77 members. Original main roster and
detail observations, plus prior retained values, are archived in
edgar_reconciliation. Failed requests retain prior paired CIK/count values and
are marked stale/unavailable. The erroneous Wagner/Warner zeros and Rogers CIK
replacement do not replace retained evidence. Successful fetches update raw
candidate evidence only. No identities are newly verified.

Vote arrays are unchanged. All539 latest attempts failed. The new counters and
attempt timestamps are preserved as votes_fail_count/votes_checked. Legacy
votes_updated values are archived in votes_refresh_provenance, and votes_updated
is null because the successful collection timestamp is unknown. No GovTrack
access remediation is attempted;403 remains deferred.

Verification:539-member audit, current-source preservation assertions,
regeneration and exact normalized549-file digest. The one-shot workflow checks
both expected review head and pinned main before pushing only the review ref.
If main moves concurrently it aborts publication rather than overwriting or
silently claiming current reconciliation. Subsequent main updates still require
inspection; this is not permission to merge/deploy. The draft remains blocked
on the documented clean-corpus/coverage/identity limitations.
