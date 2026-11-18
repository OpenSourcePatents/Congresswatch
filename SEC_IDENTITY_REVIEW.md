# SEC identity quarantine (review only)

The former `verified_auto` resolver counted every CIK returned by a name search,
including issuers and other reporting owners, across overlapping aliases. A
frequency threshold is not identity verification. The manual map is currently
empty. Do not describe these candidates as members' corporate insider activity.

Primary SEC search-index samples examined on 2026-11-18:
- Clark's selected CIK 0000842162 is LEAR CORP, an issuer. Its returned owners
  include Roelli Nicholas Jon and Cardew Jason M, not evidence identifying Clark.
- Murphy's candidate 0001838901 is displayed as Murphy Gregory J, whereas the
  roster says Gregory F. Murphy. This is a name conflict, not identity proof.
- Moylan's candidate 0001215181 is MOYLAN JAMES E JR, whereas the roster says
  James C. Moylan. This is a name conflict, not identity proof.

Reproduce with the public endpoint:
https://efts.sec.gov/LATEST/search-index?forms=4&dateRange=custom&startdt=2024-11-18&enddt=2026-11-18&ciks=0000842162
(and replace ciks with either other value). These are limited samples, not a
complete identity investigation or allegations about any member.

Main finance run 268 (44315900065) at bc3fc116d2c23fb0a07596a0ad4e0784409e5e1b
logged 87 failed SEC requests across 77 members yet passed. Five earlier failed
matches rebounded; Wagner and Warner were zeroed after alias failures. Rogers'
CIK changed while the failed count fetch retained a count from another CIK.
Review already prevents partial failures from replacing the old paired values.
That preservation does NOT validate the historical identities.

## Contract shared with OSP-API
Only counts meeting ALL conditions may affect score, flags or attributable totals:
- edgar_identity_status is `verified`.
- edgar_identity_cik is a nonempty string (after whitespace trim), exactly equal
  to edgar_cik.
- edgar_identity_evidence is a nonempty array; every entry is an HTTPS URL string.
- edgar_data_status is `available`; edgar_status is not `query_failed`.
- corporate_insider_signals is a finite nonnegative number (not bool/string).

This evidence is a deliberate curator assertion, not automatically inferred
from a URL or name. Future manual_cik_map entries must contain `cik`,
`identity_status: verified`, and `evidence: [https://...]`, after independent
review of reporting-owner identity (not merely issuer or similar name).
Automatic search can only produce `unverified_auto`; it cannot grant identity
verification. Missing metadata fails closed. Methodology:
`activity-v3-verified-sec-only`.

Raw corporate_insider_signals and edgar_cik stay unchanged as investigation
material. Legacy resolver labels are retained in edgar_legacy_status where
reclassified. Raw candidate totals have their own raw_sec_candidate_filings
field. total_insider_signals now counts eligible records only. All existing
records are unverified: raw876, eligible0 on this review snapshot. SEC eligibility
is independent of disclosed PTR trades, which still contribute normally.

539 current members audited; all five source arrays and raw CIK/count pairs
preserved exactly.38 scores change from the identity gate; one additional
score changes because a trade leaves the365-day window. No production refresh,
merge or deployment. This change does not adopt newer main snapshots after
4e506f241a14427b17273d24e90176ec8d809676; reconcile newer data separately with
source/error provenance intact. Full similarity, identity validation and source
outage resolution remain incomplete.
