# Review branch: source and regenerated data

The initially source-only branch now includes offline regenerated member scores and aggregates. All 543 current-member/aggregate JSON documents matched the reviewed local output after excluding only generated score_updated/last_updated timestamps. audit_data.py passed.

Pinned main source refreshes through ba7ccb7755d9403409bf184de8ec11481ebf43fb are reconciled: House travel freshness and bill analysis/cosponsor updates, no bill IDs or unrelated source records lost. This remains an unmerged review branch, not a production deployment. Coordinate OSP-API scoring compatibility and unresolved source-coverage gaps before deployment. No external source refresh, database migration or deployment was performed.

Rollback baseline: b9e5ac11ed0a3bae72353735c234b6fd5a19a3a7 (backup/pre-overhaul-2026-11-16).
