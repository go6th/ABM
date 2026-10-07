# Phase 4 exit criteria

Roadmap exit criteria for Phase 4, each **Met by test** or **Blocked** with the exact real-world
step. Gates run 2026-10-07 on `feat/phase4-automation`.

| Exit criterion | State | Evidence or exact step |
|---|---|---|
| At least 20 monitored accounts per pilot tenant (target to validate) | **Blocked (needs real pilots)** | instrumented (`monitor_configs` per account); onboard two pilot tenants with 20+ accounts each once real monitor sources are registered (V-P4-MON-1) |
| Sellers rate at least 70% of alerts useful (target to validate, in app) | **Instrumented; Blocked until real alerts are rated** | `alert_feedback`, `GET /v1/alerts/usefulness` (useful / rated with rated / delivered and a small-base label; 70% shown as a target), the inbox rating in the web app (Playwright) |
| A refresh costs a measured fraction of a full run, ratio reported | **Report built; the measured number needs real runs** | `governance.account_cost.refresh_ratio` from measured `tool_calls` cost with its base; "not enough data" below 10 runs each side (`test_ws6_governance.py`) |
| Backup restore and DR drill passed | **Hermetic rehearsal Done; the real drill Blocked until staging** | `ops/dr/restore_drill.py`: base backup with streamed WAL, replay, invariants, isolation suite on a restored copy: passed, rehearsal RTO 34 s, no committed rows lost (`restore_drill_report.json`). Real: Cloud SQL PITR restore and regional failover game-day (V-P4-SQL-1, V-P4-SQL-2) |

## Supporting gates

| Gate | Result |
|---|---|
| Skill self-test, `validate_assets.py` | OK (10 alert rules) |
| `lint_migrations.py` | OK, 62 files (migrations 0028 to 0032 expand only) |
| `check_rls_catalog.sql` | 0 rows |
| `ruff check` | 2 base-branch errors (`scripts/gen_observability.py`) and the RAG session's files; none in Phase 4 |
| `mypy --strict` | no issues, 485 files |
| Web typecheck, lint, unit tests | OK |
| Isolation matrix | every discovered object covered; isolation suite passes |
| `pytest tests/phase4 tests/isolation` with coverage | 462 passed; Phase 4 packages 97.1% overall, each at least 90% |
| Full `pytest -m "not eval"` | 3008 passed; 17 failures all from the other session's uncommitted RAG work (15 RAG tests, 2 static guards flagging RAG files) |
| Playwright | 12 passed (incl. alerts, subscriptions, budget, CRM opt-in) |
| `make obs-verify` equivalents | `check_runbooks.py`, `gen_observability.py --check`, alert fire/quiet tests: OK |
| Months-long simulation | 3 tenants x 26 weeks equal to the hand-written oracle; identical under 50 shuffled interleavings |
| Hermetic restore drill | passed (rehearsal) |
| Load profile | reported (`load_report.md`), no target asserted; freshness SLI 10 of 10 within 24 h in the profile |
| `scan_client_output.py` | every rendered message kind (alert, sensitive alert, digest, Summary diff, CRM payload, status page, Open Door event) |
| Gate on a refreshed package | **not run**: building the refreshed Word/PDF through the export builders is OI-P4-2 (Partial) |
| Mutation check | 552 of 609 killed (90.6%) on the pure rule modules; slots and sourcecodes not evaluable under mutmut (`mutation_report.md`) |
| No model import in the new packages | `test_ws10_gates.py` |
