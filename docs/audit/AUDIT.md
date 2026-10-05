# Integration audit

Branch `feat/integrate-gcp-production` = `feat/grading-engine` (workflow, tracker, connectors, grading)
+ `feat/job-posting-pipeline`. Audit pass 1, 2026-10-02. Every finding cites a command run on this branch.

## Gates at the start of pass 1

| Gate | Result |
|---|---|
| skill `selftest.sh` | `selftest OK` |
| `validate_assets.py` | `OK: 33 coverage rows, 5 product grades, 8 routes, 10 alert rules` |
| `lint_migrations.py migrations` | `OK: 16 file(s)` |
| `check_rls_catalog.sql` | no rows |
| `mypy` (project) | no issues, 114 files |
| `ruff check .` | **red**: errors in tracker modules, `0008`, `scripts/gen_tracker_docs.py`, tracker tests |
| `ruff format --check .` | **red**: 7 files |
| `pytest tests/isolation` | 19 passed |
| `pytest -m "not eval" --cov` | 582 passed, 12 skipped, 1 xfailed (defect 5); 95.15% |

## Findings

### F-1 Two research graphs; the runtime runs the wrong one (blocking)
`agents/runner.py` executes the starter graph `agents/graph.py` (baseline, four fetch branches with a
`NotImplementedError` broker, distill). Workflow v1 (`workflow/engine.py`, 13 stages from
`config/workflow_v1.yaml`) is imported by nothing; every stage handler is `NotConfigured`.

### F-2 Two run tables (blocking)
`account_runs` (starter: orchestration status, idempotency, wallet reservation, budget, ledger) and `runs`
(data layer: lifecycle trigger, refresh parent; the tracker's `coverage_rows.run_id` references it). Nothing
links them. Connectors, budget and grading use `account_runs`; the tracker uses `runs`.

### F-3 The tracker's completion guard never fires (defect)
`check_run_completion()` (migration 0008) tests `NEW.status = 'COMPLETED'`, but `runs.status` only allows
lowercase `completed`. Reproduced: a run with no tracker seeded moved to `completed`.

### F-4 Built components are unwired (orphans)
`docs/architecture/module-map.md`: nothing on the runtime path imports `connectors.factory`,
`grading.integration`, `grading.adapters`, `tracker.service`, `workflow.engine`, `workflow.evidence_gate`,
`workflow.stages.s05_jobs`, or any of `jobs.*` except models and window. `storage` is an empty package.
`tracker.guard`, `tracker.render`, `workflow.transitions`, `db.source_ids` have no runtime caller.

### F-5 Ports without adapters
`workflow.ports` (LlmPort, ConnectorPort, SourceRepo, FactRepo, PeopleRepo, PostingRepo, DetectionRepo,
CoverageRepo, PayloadStore, BuilderPort, GatePort, WalletPort), `jobs.ports` (PostingSourcePort,
PageReadPort, PayloadStore, PostingRepo, CoverageEvidencePort, GradingPort, ExtractorPort, JudgePort,
RunInputPort), `tracker.ports` (CallLedgerPort, RunInputPort). Only FactRepo has an adapter
(`grading.integration.GradingFactRepo`). No `LlmPort` adapter exists anywhere.

### F-6 Duplicate ports for one concern
Three `PayloadStore` protocols (`connectors.payloads`, `workflow.ports`, `jobs.ports`) with three
different signatures; three `NotConfigured` classes (`workflow.ports`, `tracker.ports`, `jobs.errors`)
plus `grading.errors.NotConfigured`; two `Clock` protocols with different methods (`now()` vs `today()`).

### F-7 Rule duplicates already merged (no action)
Lead rule (`tracker.leads` delegates to `grading.leads`), quote/URL/figure checks (`workflow.evidence_gate`
delegates to `grading.spans`), engine (`agents.grading` facade): merged in the grading work package.
Remaining: the starter `agents.graph.ledgered_call` and `connectors.service.call_operation` both write the
ledger, through the same `Ledger` class (one implementation, two entry points).

### F-8 Missing tables
`job_postings`, `people`, `tech_detections` (data-layer plan, P1-1, P1-6). The jobs pipeline has a
`PostingRepo` port and no table.

### F-9 Guard bypass paths
No production code writes `facts`, `coverage_rows` or postings outside their repos (grep), but nothing
enforces it: no import contract, no grep test.

### F-10 Local and third-party runtime assumptions (see inventory below)

### F-11 Tracker row ownership is not enforced
`TrackerService.record_outcome` treats any `Actor.STAGE` caller as the row's owner; the checklist's
`owner_stage` is read and ignored. Any stage can upgrade any row.

### F-12 Gap-loop progress assumes a changed proof hash is more proof
`tracker/gap.py`: same status plus a different proof hash counts as progress; nothing proves the new proof
is a superset. A stage that rewrites proof could keep the gap loop "progressing" (the loop cap still bounds it).

## Local-service inventory and GCP replacement (D-20 proposal)

| Found | Where | Replacement |
|---|---|---|
| Inngest client, event sends, `/api/inngest` route | `orchestrator/functions.py`, `api/main.py`, `api/routes/runs.py`, `compose.yml` | `RunScheduler` port; Cloud Tasks adapter (D-18) |
| `boto3` S3 payload store | `connectors/payloads.py` (`S3PayloadStore`), `pyproject.toml` | Cloud Storage adapter |
| SeaweedFS S3 emulator, Redis, Inngest dev server | `compose.yml` | none at runtime; Postgres only under `tests/` |
| `InMemoryPayloadStore`, `LocalPayloadStore` | `connectors/payloads.py` | test-only; refused at startup in staging/production (W4) |
| `InMemoryRateStore`, `InMemoryBreakerStore` defaults | `connectors/factory.py` | shared store (Redis exists as `RedisRateStore`; decide Memorystore vs Postgres in D-20) |
| `.env` and env-only settings, password DSNs | `config.py`, `Makefile`, `compose.yml`, CI | Secret Manager references on Cloud Run; Cloud SQL connector with IAM auth |
| `localhost:5432` DSNs | CI, Makefile, tests | tests only |

## Comment-free files

Measured in pass 2 by `scripts/check_comments.py` (added in Step B).

---

# Audit pass 2 (2026-10-03)

Every row was rechecked against the code on `feat/integrate-gcp-production`, in the single project
folder `/home/sakib/ABM Generator`, with the commands in the gates table.

## Gates at pass 2

| Gate | Command | Result |
|---|---|---|
| Skill self-test | `bash .claude/skills/.../selftest.sh` | `selftest OK` |
| Assets | `validate_assets.py` | `OK: 33 coverage rows, 5 product grades, 8 routes, 10 alert rules` |
| Migration lint | `lint_migrations.py migrations` | `OK: 22 file(s)` |
| RLS catalog | `check_rls_catalog.sql` after `alembic upgrade head` | no rows |
| Lint, format | `ruff check .`, `ruff format --check .` | clean |
| Types | `mypy` (strict, `src`) | no issues |
| Import contracts (W1) | `lint-imports` | 7 kept, 0 broken |
| Single write paths (W1) | `tests/system/test_single_write_paths.py` | pass |
| Docstrings, comments, guarantees | `interrogate src`, `check_comments.py`, `check_guarantees.py` | 100%; all files; 0 dangling, 0 untagged guards |
| Loop inventory (W8) | `gen_loops.py --check` | current; no unannotated loop |
| Security, dead code | `bandit -c pyproject.toml`, `vulture` (whitelist) | clean |
| Isolation (hard gate) | `pytest tests/isolation -x` | 20 passed |
| Everything except evals | `pytest -m "not eval" --cov --cov-fail-under=90` | 1074 passed, 12 skipped (live/eval), 94.41% |
| Terraform | `make infra-fmt infra-validate` (Docker) | fmt clean; dev, staging, production valid (google 6.50.0) |
| Image | `docker build`, `scripts/smoke_image.sh` | non-root uid 10001, `/readyz` 200, SIGTERM exit 0 |
| CI workflow | `actionlint` | clean |
| Port contracts | fake-gcs-server and Cloud Tasks emulator | GCS and Cloud Tasks adapters pass |
| Clean clone | fresh `git clone`, `uv sync --frozen`, every gate on a new scratch database | see BUILD_LOG 2026-10-03 |

## Findings at pass 2

| ID | Pass 1 | Pass 2 | Evidence |
|---|---|---|---|
| F-1 | runtime ran the starter graph | **Fixed**: the starter graph, runner and Inngest functions are removed; the worker runs Workflow v1 (13 stages) | `workflow/runner.py`, `tests/system/test_whole_run.py` |
| F-2 | two unlinked run tables | **Fixed**: one id, both rows created together by `runs.lifecycle` | ADR-0016, `tests/runs` |
| F-3 | completion guard never fired | **Fixed** (0010) | `tests/db/test_run_completion_guard.py` |
| F-4 | built components unwired | **Fixed**: connectors, tracker, grading, jobs all on the run path; remaining unimported modules are entry points or spec ports (module map) | `docs/architecture/module-map.md` |
| F-5 | ports without adapters | **Fixed**: LLM, scheduler, payloads, secrets, database, checkpointer adapters; builders and gate remain `NotConfigured` by design (OI-BLD-1) | `platform/` |
| F-6 | duplicate ports | **Fixed**: one `PayloadStore`, `Clock`, `NotConfigured` | `platform/ports.py` |
| F-7 | rule duplicates merged | no action | |
| F-8 | missing tables | **Fixed** (0010, 0012) | |
| F-9 | guard bypass paths | **Fixed**: single write paths enforced (W1) | `test_single_write_paths.py` |
| F-10 | local runtime pieces | **Fixed**: Inngest, boto3, S3 store, SeaweedFS removed; GCP adapters behind `container.py`; `assert_gcp_runtime` (W4) | ADR-0017 |
| F-11 | tracker ownership not enforced | **Fixed**: owner pinned at seeding; T7 tests | `tests/tracker/test_lying_stages.py` |
| F-12 | gap progress by hash | **Fixed**: progress counts proof calls | `tracker/gap.py`, `tests/tracker/test_edges.py` |

New findings during pass 2, all fixed in this pass: the rate limiter spun on sub-microsecond waits;
a canary echo was dropped without being counted; the cloud container never built the connector
runtime; the SIGTERM handler replaced uvicorn's; two slice deliveries could run one run at once
(run lease now taken per slice); a `runs` <-> `workflow` package cycle; bidirectional characters in
a regex literal (bandit B613).

## Spec compliance

`docs/audit/SPEC-COMPLIANCE.md` (tracker, workflow, jobs) is current; remaining partial items are
the open items in `docs/open-items.md`.
