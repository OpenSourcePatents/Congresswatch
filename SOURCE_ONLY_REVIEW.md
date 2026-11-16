# SOURCE-ONLY REVIEW BRANCH: NOT DEPLOYMENT-READY

This branch contains backend/frontend source changes but deliberately excludes the regenerated member JSON. It is NOT the complete deployment snapshot. The full local overhaul is preserved in congresswatch-overhaul.patch and congresswatch-changed-files.zip delivered separately.

Before deployment, coordinate OSP-API's score methodology, run recompute_scores.py, build_aggregates.py and audit_data.py against the intended source snapshot, inspect the resulting data changes, then publish code and derived data together. Do not merge/deploy this review branch alone expecting corrected scores.

Baseline and rollback: b9e5ac11ed0a3bae72353735c234b6fd5a19a3a7 (backup/pre-overhaul-2026-11-16).
Full local implementation commit: b1c8830266b1058ba03f84404809aa8d07f8672e.

No database migration or workflow dispatch performed.
