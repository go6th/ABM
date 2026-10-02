# Build log

Current phase: 0 (Prove)  
Current work package: foundation repository integration  
Last updated: 2026-10-02

## Built

- Created the required pre-cleanup archive at `../backup-before-cleanup-20261001-2323.zip`.
- Preserved legacy and unclassified material locally under `_review/` (gitignored); removed generated caches and the superseded builder skill copies.
- Kept the platform skill under `.claude/skills/account-intelligence-platform/` and preserved the three confidential source documents under ignored `docs/sources/`.
- Integrated the tested starter API, graph, runner, database session, migrations, Docker files, and database tests.
- Added Alembic revisions that apply the starter SQL and run checkpointer setup before RLS; the runtime role password comes from `APP_RW_PASSWORD`.
- Verified Inngest SDK `0.5.19` serving signature and wired its FastAPI endpoint at `/api/inngest`.
- Added the grading engine and worked-example/property tests, project docs, repository hygiene, and CI.

## Commands run and results (baseline before the 2026-10-02 follow-up)

| Command | Result |
|---|---|
| `zip -rq ../backup-before-cleanup-20261001-2323.zip . -x '.git/*' 'node_modules/*' '.venv/*'` | Pass; non-empty 4.1 MiB archive verified. |
| `bash .claude/skills/account-intelligence-platform/scripts/selftest.sh` | Pass; `selftest OK`. |
| `python .claude/skills/account-intelligence-platform/scripts/validate_assets.py` | Pass via skill self-test; 33 coverage rows, 5 product grades, 8 routes, 10 alert rules. |
| `uv lock` and `uv lock --check` | Pass; resolved 85 packages and verified the lockfile. |
| `.venv/bin/uv sync --frozen` | Pass; installed the locked dependency set. |
| `ruff check .` on Python 3.11 and 3.12 | Pass after the final integration-fixture edit. |
| `mypy src` on Python 3.11 and 3.12 | Pass after the final integration-fixture edit; 13 source files checked. |
| Offline unit tests on Python 3.11 and 3.12, `--cov=src.agents.grading --cov-fail-under=100` | Pass; 22 passed and 100% grading-module coverage on both versions. |
| FastAPI OpenAPI route inspection | Pass; `/healthz`, `/v1/runs`, `/api/inngest` present. |
| GitHub API action revision lookups | Pass; checkout, setup-python, and setup-uv SHAs obtained and pinned. |
| `make check` | Stops at the database gate: `psql: command not found`, after self-test, asset validation, migration lint, Ruff, and mypy pass. |
| `docker compose up -d db` / container build | Not available; Docker reports `unknown shorthand flag: 'd' in -d`, and daemon access is denied. |
| Database migrations/catalog, isolation suite, clean-clone proof | Not run; PostgreSQL service/client are unavailable. |

## Defects and open items (baseline before the 2026-10-02 follow-up)

- Starter defects 1 and 2 are retained as fixed: reducer on `spent_usd`, role-scoped RLS policies.
- Strict expected failures remain for duplicate concurrent billing and idempotency-key body mismatch.
- No broker/model adapters are implemented in this milestone; networked paid calls were not run.
- Inngest SDK route serving was verified locally; full Inngest event and time-slicing behavior remains open under V4.
- D-01 through D-11 remain open as described in the skill's `references/12-open-decisions-and-verification.md`.
- No live tenant data or real paid service was accessed.
- The whole-suite coverage target remains 80%; its measured project-wide value is unverified until the database-backed suite can run.

## Follow-up: Phase 0 and Data layer v1 audit (2026-10-02)

The earlier database-unavailable notes above describe the pre-restore baseline. On the restored feature branch, the data-layer implementation, audit status and local database tests were completed. No live accounts, provider calls, broker credits or cloud resources were used.

### Implemented

- Added expand-only migration `0003_data_layer_v1.sql` and its Alembic revision for source counters/records, evidence, coverage rows, people, postings, detections, raw payload metadata and tool-call intent tracking, with composite tenant relationships, forced RLS and append-only/coverage guards.
- Added tenant-scoped repositories for stable normalized-URL source IDs, fact/evidence transactions, coverage seeding/proof, person deduplication/merge, postings and technology detections.
- Added neutral-key local compressed raw payload storage with content hashes and pull-once metadata.
- Added model-tier routing, people-source adapter switching, benchmark protocols, a synthetic-only golden-account loader, the counsel-facing people-data draft, environment configuration, and local staging script.
- Added a LangGraph/Inngest durability ADR and a process-kill/resume test. The runner persists `PARTIAL` rather than falsely reporting `COMPLETED` when required High-priority coverage proof is missing.
- Corrected the psycopg 3 test setup to use cursor-level `executemany` and registered the database test marker.

### Follow-up commands and results

| Command | Result |
|---|---|
| `uv run --frozen ruff check .` | Pass. |
| `uv run --frozen mypy src` | Pass; 21 source files. |
| `bash .claude/skills/account-intelligence-platform/scripts/selftest.sh` | Pass; 33 coverage rows, 5 product grades, 8 routes, 10 alert rules. |
| `uv run --frozen python .claude/skills/account-intelligence-platform/scripts/validate_assets.py` | Pass. |
| `uv run --frozen python .claude/skills/account-intelligence-platform/scripts/lint_migrations.py migrations` | Pass; 3 SQL migrations. |
| `MIGRATOR_DATABASE_URL=postgresql://postgres:dev@localhost:5433/ai CHECKPOINT_DATABASE_URL=postgresql://postgres:dev@localhost:5433/ai APP_RW_PASSWORD=local-dev-only uv run --frozen alembic upgrade head` | Pass on an isolated local PostgreSQL 16/pgvector instance. |
| Direct execution of `.claude/skills/account-intelligence-platform/scripts/check_rls_catalog.sql` using psycopg | Pass; `RLS catalog: no violations`. |
| `PGPORT=5433 PGPASSWORD=dev PGADMINUSER=postgres TEST_DB=ai APP_RW_PASSWORD=local-dev-only MIGRATOR_DATABASE_URL=postgresql://postgres:dev@localhost:5433/ai CHECKPOINT_DATABASE_URL=postgresql://app_rw:local-dev-only@localhost:5433/ai uv run --frozen pytest -m 'not eval' --cov=src --cov-fail-under=80` | Pass; 79 passed, 1 expected failure, 81.85% coverage. Includes RLS, data-layer, full-text, raw payload, coverage, runner and five process-kill/resume tests. |
| `make check` | Static gates pass; wrapper cannot continue because the host `psql` client is not installed. Equivalent migration, catalog, isolation/integration and coverage checks above passed. |
| `git diff --check` | Pass. |

### Still blocked

- Model-split results: three September accounts, paired run outputs, reviewed citation validity, model cost/time records and the decision owners' approval.
- People-source results: same three account snapshots, usable-person rubric, broker cost/credit records and approval to spend.
- Real golden accounts: three to five September outputs, source evidence, known-correct facts, analyst grades and dated analyst review.
- Legal approval: counsel's lawful-basis, retention, deletion/objection and broker-term decisions.
- Human sign-off on the decision log and Phase 1 backlog estimates/agreement; analyst-role decision remains open.
- Cloud account/region/credentials and a local `.env` are not supplied; no cloud or local staging deployment was claimed.

See `docs/STATUS.md` for the item-by-item evidence table and remaining gate state.
