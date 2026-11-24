# Bill-text integrity quarantine

Main bill commit 506ac59adf260d987646832cdcff7d8a9b762225 publishes HR11037-119
with keywords `obj endobj stream endstream ... pdf ... hhs`, similarities of
0.980/0.979/0.979 to HR10195/HR9592/HR10507, and a Healthcare donor keyword hit
on `hhs`. Those are not reliable legislative findings.

Main fetchers decode LegiScan base64 PDF as UTF-8 with replacement and truncate
at50,000 characters; Congress text is likewise treated as text. The workflow
restores its old all_bills.json cache. Run44345891216 reported9,943 initial
cache entries,9,972 final entries,4,874 cache hits and zero errors, then saved
cache key bills-cache-262. A green run does not validate prose extraction. Structural PDF tokens dominate622
published rows across291 members. This is evidence of binary document analysis,
not evidence of shared bill language. Exact source route for HR11037 cannot be
proven from the published record: it lacks the text URL, body and document ID.
The live Congress PDF URL attempt returned404; no clean HR11037 text was fetched.

The review already correctly decodes newly fetched PDFs, but previously only
invalidated HTML-contaminated cache. This correction rejects whole corrupted
raw/cleaned text at cleaning, cache, vectorization, similarity and donor boundaries.
It never just removes PDF tokens then trusts remaining gibberish. Original
cache evidence is retained; successful refetch archives replaced evidence.

Because a polluted TF-IDF corpus changes vocabulary/IDF even for cleaner rows,
all5,405 legacy bill similarity results are suspended pending clean-corpus refit.
Original keywords/similarities/donor analyses are retained under
analysis_provenance.original_fields.622 directly polluted rows additionally
lose attributed donor-keyword findings and has_text/keywords. No claim is made
that remaining donor matches have independently verified source text.

Future analysis emits analysis_version bill-text-integrity-v1; analyzed and
model_matching_unavailable statuses distinguish complete and restricted matching.
A validated corpus/refetch is still needed; this change does not supply it.
UI distinguishes suspension from a genuine no-match result and does not render
archived false matches as findings. Score methodology is activity-v4-bill-integrity;
its arithmetic is unchanged, but uses corrected/quarantined inputs.

## Preserved source updates
Pinned source changes incorporated without merging main:
- Senate trades5c4f4f3a: four additions.
- House travelcb6a6f65: three additions plus source timestamps.
- House trades06455060: DeLauro's four Oct2 ADSK/IBKR/NFLX/ZTS records,
  each $1,001–$15,000, preserved verbatim.
- Bills506ac59a: all latest bill identities, titles, actions, cosponsors and URLs
  preserved in original order, including malformed IDs; never merge by bill ID.

No unsafe finance/vote refresh metadata is adopted. Prior SEC quarantine stays
intact. Counts after reconciliation:8,808 trades (231 scanned placeholders),
5,405 bills. Former-member detail files are included in quarantine and verification.
All votes/donor lists/raw SEC CIK-count pairs remain identical to review baseline.

Verification: whole539-member audit, source-field/array preservation comparisons,
PDF-boundary checks against a real repository PDF, all622 polluted keyword rows,
15 template texts, compilation and UI checks. Full live similarity refetch is
not run: API credentials and historical cache are unavailable locally. No main
merge or production deployment. Review workflow is offline, pinned, exact-ref
restricted and digest-gated. Existing Vercel integration may create a preview.
