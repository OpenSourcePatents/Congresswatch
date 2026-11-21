# Official bill similarity rebuild (local checkpoint)

This is a review-only reconstruction. Nothing in this checkpoint changes live
data, the website, route outputs, cached historical findings, or Actions. Do not
merge or run a production write just to generate this evidence.

On the reconstructed snapshot, 189/200 frozen-by-hash CRS Identical pairs were
in the top three for the selected test direction. Of the 11 misses, five
qualified but were outside the top three, five shared fewer than 32 contiguous
words, and one had insufficient relative shingle coverage. The source versions
and per-pair results are retained in the compressed replay result. This
measures recall on known linked positive pairs, not overall precision. It is
not a like-for-like comparison with earlier datasets or parameterizations.

## Reproduce

Use Python 3.12 or newer and install the versions in
`requirements-official-prose.txt` in a local virtual environment. Then:

```sh
python rebuild_official_corpus.py --refresh
python benchmarks/bill_similarity/official_controls.py
python benchmarks/bill_similarity/official_retrieval_eval.py .cache/current-crs-replay.json
python stage_official_bill_findings.py --member B001260 --output .cache/staged-buchanan
python audit_staged_official_bills.py .cache/staged-buchanan
```

Without `--refresh`, the corpus command reuses the local, ignored archives and
indexes. The run inspects 24 GovInfo bulk partitions. It does not publish from
a subset or silently replace an incomplete corpus. Missing archive members
must be on the official index and recover individually; unexpected archive
names, missing records, unreadable XML or identity conflicts block evaluation.
Checkpoint progress can be resumed with the same arguments plus `--resume`,
provided the corpus, roster, code, dependency versions and staged hashes still
match. The final output manifest is written only after all chosen members finish.

The corpus preserves SHA-256 for raw XML and extracted text, exact official
version, date when recorded, and URLs. Raw parsed bodies and archives are not
included in the source patch. Re-fetch and compare the manifest to regenerate;
updated official indexes may produce a new snapshot.

The 2026-11-21 local audited snapshot has 20,193 metadata identities and 23,530
full-text versions across 20,032 bills. It excluded 24 unconsolidated amendment
documents and used two individually recovered index-listed records. See
`benchmarks/bill_similarity/rebuild_source_manifest.json` for the input SHA-256
fingerprints and partition-level counts. All source audits completed without
errors. That is a snapshot, not a completeness guarantee about future data.

Version policy is enrolled full text first; otherwise the newest dated full
body, with an explicit deterministic tie-break for same-day versions. An
engrossed amendment document is an instruction, not a consolidated body.
Wording inside explicit deletion/strike markup or a table of contents is
excluded. This is the published legislative proposal, not law as in effect.
The Bill Status sponsor's Bioguide ID establishes the join; title or sponsor
name alone does not.

Similarity shows shared wording, not policy equivalence, copied percentages,
a vote, authorship, coordination, or wrongdoing. Candidates are bounded sparse
cosine results (at least 0.30; at most 40 per target). Findings must share 32
contiguous normalized words and at least 20% of distinct 12-word shingles on
one side; up to three qualifying findings are returned. Both actual source
excerpts remain available. A long exact passage within an omnibus may have
low whole-document cosine and not be retrieved; the named embedded-passage
control tests the passage verifier directly, not whole-corpus recall.

No verified primary ALEC corpus is in scope; that field remains unavailable,
not a successful zero. Donor alignment is independently unavailable in staged
records; this tool does not assign donors a motive or modify scores. Historical
and manually maintained data remain unchanged until separately reconciled.
Staged bill-only outputs cannot overwrite the repository's data tree.

Before full use, reconcile the latest authorised draft and main source fields,
preserve superseded and manual history, verify all member data, actual browser
rendering, exact route schemas and 539-member repo/API parity, and keep both
merges on hold for owner review.

## Local assembly (not publication)

After the full-roster bill stage and evidence audit, `assemble_local_official_review.py`
can join it to an explicitly pinned review and upstream commit, in a new output
directory. This copies newer known nonbill source fields, keeps disabled SEC
identity hypotheses separate from verified inputs, keeps unchanged vote records
and records the failed check separately from collection, and archives both
the manually maintained review bill records and raw latest upstream bill records.
The output is intentionally marked unfinalized: it has no fresh score or aggregate
regeneration and cannot be treated as production-ready. A roster change, held
vote data change or stage digest mismatch halts the assembly rather than guessing.

A local assembler run can be rescored in its isolated root using the existing
`recompute_scores.py` then run `build_aggregates.py`. Detailed records retain
each full displayed excerpt. The flat bill aggregate stores match identities,
provenance and coverage with an explicit detail path, not duplicate passage
text; the aggregate has a fail-closed publication size check. Current consumer
compatibility should still be verified before deployment.

## Release gates

- Recheck maintainer authorization and exact branch heads; retain a rollback
  commit. Reconcile any new upstream inputs in a new pinned snapshot.
- Run the full staged-evidence and source-preservation audits, rebuild local
  scores and aggregates and verify roster/detail/stats parity and OSP-API
  parity. Do not treat unverified SEC name matches as member activity. Keep
  the GovTrack collection failure and ambiguous archived source values explicit.
- Validate desktop and narrow mobile on a real hosted preview, with the exact
  artifacts served. Confirm browser interactions, source links and API response
  contracts on the same immutable dataset; account for URL-scoped API/CDN caches.
- Wire and independently exercise a scheduled regeneration path that rejects a
  partial GovInfo snapshot, preserves other pipelines' inputs, and never
  publishes stale derived data over an advancing main. This checkpoint does
  not change the scheduled production workflow.
- After explicit authorization, prefer a normal authenticated Git push to the
  existing draft branch (not main, never force); verify the exact remote head
  and hosted checks. Keep both merges blocked until the coordinated release
  and rollback order has been reviewed.
