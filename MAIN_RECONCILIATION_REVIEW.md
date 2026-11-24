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

## 2026-11-21 local official-prose integration candidate

- Existing draft head: `cd73a42e0d8982869fce11d25b0eed46ae77e6e0`.
- Verified implementation parent: `ceedc6c3837d1ecbbb4099fb2ff7cf90b4e09672`.
- Pinned current main (second parent): `598849e5e7e845eaa6359fe7c74c4b4af335d989`.
- Common ancestor: `506ac59adf260d987646832cdcff7d8a9b762225`.
- GovInfo corpus SHA-256: `e155135e3741949d1083fe4f4790faa62682aeff277a289fa839693ce4800b8a`.
- Verified staged 539-profile manifest SHA-256:
  `3f451e4d031b3dbe28070b8efd6738a69668714246e235faaf206baca88743d9`.

The merge uses the audited assembled data, not an ours strategy. Main changed
only data in 545 paths; all 542 merge conflicts were resolved explicitly
against the validated snapshot. All known newer audited trade, travel, donor
and financial fields were preserved. All 53,681 vote rows were unchanged;
new failed attempt metadata is stored separately, not relabeled collection.
Latest SEC candidate observations and timestamps remain raw and unverified,
with no candidate-only signals or trade flags promoted. Main's unresolved
candidate file and travel cost manifest are retained byte-for-byte.
Two known ambiguous travel OCR costs remain preserved as upstream raw
observations, not corrections in the bill-similarity scope.

The two original active bill record streams are preserved in member history
(1,078 per-source archives); six former profiles and their historical rows are
retained. Archived vote, bill and SEC fields are not given new evidential
meaning. Superseded flags, derived scores and aggregates remain available
in both exact Git parents, not reintroduced as findings. Legacy
`last_updated` on main equals its raw `data_updated` for all 539 members;
it remains retrievable in the archived raw observation rather than replacing
the source-specific active timestamps.

Local validation: complete official evidence replay, 539 member audits,
19,947 sponsored bill rows, 17,130 matched passage records, exact roster and
score checks, 20,007 compact flat bill rows, and 8,821 aggregate trades.
The flat bill aggregate omits duplicated passage strings; every excerpt is
in its linked member detail. The catalog/coverage files seed the guarded
scheduled refresh from the verified official snapshot, not an install of
the explicitly offline-only test seal. Nothing here was pushed, deployed,
merged in GitHub, or exercised in hosted Actions.
