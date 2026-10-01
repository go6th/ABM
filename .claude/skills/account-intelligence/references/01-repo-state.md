# 01 Repository map and current state

Snapshot written 1 October 2026 at commit `8cd123b` on branch `feat/data-layer-v1` (pushed to `go6th/ABM`, private). Check `git log --oneline -15` and `docs/BUILD_LOG.md` for anything newer.

## Map

| Path | What it is | Status |
| --- | --- | --- |
| `app/settings.py` | Environment-only settings; `LIVE_KEYS` lists keys a live run needs | Phase 0, done |
| `app/rules/` | Pydantic schemas and loader for `config/*.yaml`; `RuleBundle.version` is what a run pins | Phase 0, done |
| `config/` | `checklist.yaml` (33 rows), `grading.yaml`, `routing.yaml` (INTERNAL), `output_spec.yaml`, `models.yaml`, `people_scope.yaml`, `lanes.yaml` | Canonical rule config |
| `app/golden/`, `tests/evals/golden/` | Golden account schema, loader, three SYNTHETIC accounts | Real accounts: needs analyst |
| `app/llm/`, `app/brokers/`, `app/people.py` | Provider interface plus fake; broker session (pull once) plus recorded double; people merge | Phase 0 skeletons |
| `app/benchmarks/`, `app/decision_log.py` | Model split and people source harnesses; evidence writer refusing non-evidence | Phase 0, offline only |
| `app/workflows/`, `app/worker.py` | Temporal hello-world workflow and worker | Phase 0 |
| `app/db/` | Data layer v1: `base.py` (conventions, `DEFAULT_TENANT_ID`), `types.py`, `enums.py`, `url.py`, `models/`, `migrations/` (Alembic, own version table) | Phase 1, in progress |
| `tests/app/` | All tests for `app/`; `tests/app/db/` need a database (`PG_BIN` or `TEST_DATABASE_URL`) | |
| `infra/terraform/`, `scripts/deploy.sh` | Cloud-neutral environments; one-command deploy (stops at undecided cloud) | Phase 0 |
| `docs/` | `decision_log.md`, `data_handling.md` (draft for counsel), `phase1_backlog.md`, `PHASE0_STATUS.md`, `AUDIT.md`, `docs/data_layer/PLAN.md`, `docs/data_layer/CONVENTIONS.md`, `docs/BUILD_LOG.md` | |
| `src/abm_generator/`, `migrations/`, `tests/unit`, `tests/isolation`, `tests/spike` | **Legacy** from the older roadmap (RLS isolation core, tool ledger, slice runner). Not extended; port deliberately | Legacy |
| `.claude/skills/account-intelligence/` | This skill (entry point) | |
| `.claude/skills/account-intelligence-platform/` | Platform reference library (ARCH v2 era): references 00 to 13, assets, scripts, starter code | Reference |
| `.claude/skills/account-intelligence-builder/` | First Phase 0 skill; superseded by this one | Superseded |

## Roadmap Phase 0 (details in `docs/PHASE0_STATUS.md`)

Built and tested. Needing a person: decision log sign-off, real golden accounts, live benchmark runs, counsel review of `docs/data_handling.md`, cloud and region choice, first green GitHub Actions run, local docker stack run.

## Roadmap Phase 1, work item "Data layer v1" (the person's numbered steps)

| Step | What | Commit | State |
| --- | --- | --- | --- |
| 1 | Plan (`docs/data_layer/PLAN.md`) | `5be9c63` | Done |
| 2 | Local Postgres 16 and MinIO in compose; Makefile `up`, `down`, `db-shell` | `8d61191` | Done; `make up` not run (no docker access here) |
| 3 | Not received | | Ask if it existed |
| 4 | Alembic in `app/db/migrations`, `make migrate`, `make migration m=...` | `5156060` | Done |
| 5 | Shared conventions (UUID keys, timestamps, JSONB object type) | `33c5f07` | Done |
| 6 | Enums as CHECK-constrained text, synced with config | `3aa6d66` | Done |
| 7 | `intel.accounts` (migration 0002) | `a62f96d` | Done |
| 8 | `intel.runs` (migration 0003) | `8cd123b` | Done |
| next | Per the plan: sources (stable ids), source_reads, facts and fact_sources, coverage_rows, people, job_postings, tech_detections, intel_internal raw_objects and run_budgets, never-delete triggers, object storage, data dictionary | | Ask R-01 first |

Migration heads: `0001` schemas, `0002` accounts, `0003` runs. Each new revision: `make migration m="..." rev=000N`, then edit by hand (`02-data-layer-playbook.md`).
