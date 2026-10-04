# Build log

Current phase: 0 (Prove)  
Current work package: foundation repository integration  
Last updated: 2026-10-01

## Built

- Created the required pre-cleanup archive at `../backup-before-cleanup-20261001-2323.zip`.
- Preserved legacy and unclassified material locally under `_review/` (gitignored); removed generated caches and the superseded builder skill copies.
- Kept the platform skill under `.claude/skills/account-intelligence-platform/` and preserved the three confidential source documents under ignored `docs/sources/`.
- Integrated the tested starter API, graph, runner, database session, migrations, Docker files, and database tests.
- Added Alembic revisions that apply the starter SQL and run checkpointer setup before RLS; the runtime role password comes from `APP_RW_PASSWORD`.
- Verified Inngest SDK `0.5.19` serving signature and wired its FastAPI endpoint at `/api/inngest`.
- Added the grading engine and worked-example/property tests, project docs, repository hygiene, and CI.

## Commands run and results

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

## Defects and open items

- Starter defects 1 and 2 are retained as fixed: reducer on `spent_usd`, role-scoped RLS policies.
- Strict expected failures remain for duplicate concurrent billing and idempotency-key body mismatch.
- No broker/model adapters are implemented in this milestone; networked paid calls were not run.
- Inngest SDK route serving was verified locally; full Inngest event and time-slicing behavior remains open under V4.
- D-01 through D-11 remain open as described in the skill's `references/12-open-decisions-and-verification.md`.
- No live tenant data or real paid service was accessed.
- The whole-suite coverage target remains 80%; its measured project-wide value is unverified until the database-backed suite can run.

## API v1 tenant-core increment (2026-10-04; partial)

- Added tenant-claim JWT validation with current membership re-check and a 60-second cache,
  explicit v1 route policies, typed problem-details responses, request IDs, and safe response
  headers.
- Fixed run idempotency to claim `(tenant_id, key)` before account/run writes, compare canonical
  request hashes, retain the exact original response body, and roll the claim back with a 402.
- Added pre-spend domain syntax/blocklist/public-address checks, conditional run-status ETags,
  typed service ports/fakes, and a configurable token-bucket primitive.
- Removed the starter's illustrative 6/18 credit prices from the API path. D-04 prices now have
  no assumed defaults; run creation returns a typed 503 until explicit prices are configured.
- Added expand-only `0007` for the tenant-RLS-protected idempotency table. The migration has not
  been applied to PostgreSQL in this environment.
- Documented implemented and blocked surfaces in `docs/api/core.md`. Account create/list,
  cancellation, tracker/delivery, audit, API keys, rate-limit wiring/values, operator plane,
  console, and the workflow-owned transaction/outbox remain incomplete.

| Command | Result |
|---|---|
| `bash .claude/skills/account-intelligence-platform/scripts/selftest.sh` | Pass; `selftest OK`. |
| `uv run --frozen python .claude/skills/account-intelligence-platform/scripts/validate_assets.py` | Pass; 33 coverage rows, 5 product grades, 8 routes, 10 alert rules. |
| `uv run --frozen pytest -m 'not requires_postgres and not eval' -q` | Pass; 72 passed, 121 deselected. |
| `uv run --frozen ruff check .` | Pass. |
| `uv run --frozen ruff format --check .` | Pass; 84 files already formatted. |
| `uv run --frozen mypy` | Pass; 38 source files checked. |
| `uv run --frozen python .claude/skills/account-intelligence-platform/scripts/lint_migrations.py migrations` | Pass; 12 SQL files. |
| `uv run --frozen alembic history` | Pass; revision chain ends at `0007`. |
| `git diff --check` | Pass. |
| `pytest tests/isolation`, applied migrations, RLS catalog, PostgreSQL concurrency tests | Not run; `psql` and a PostgreSQL service are unavailable. |

- This is not a completed API v1 work package or Phase 0 exit. No operator cross-tenant access
  was added. The full acceptance path and dispatch-failure reconciliation remain unproven.

## Verification update (2026-10-04)

- Added focused quality-gate unit tests covering ordering, target selection, deterministic hashes,
  verdicts, bounded defects, scope validation, and explicit adapter failures.
- Updated run-creation integration tests for the current FastAPI request/response contract,
  typed `APIProblem` errors, and explicit test-only pricing.
- Full gate-package line and branch coverage is 100%; this is not project-wide 100% coverage or
  proof of absence of defects.
- The full database-backed check passed against a uniquely named scratch database: migrations
  through `0007`, Alembic model-drift check, RLS catalog, lint, formatting, mypy, and the
  non-eval test suite. PostgreSQL scratch resources were cleaned up.
- Project-wide coverage is 89.38%. In particular, API run routes are at 71%, orchestrator
  functions at 40%, and API rate limiting at 74%; production integration and unavailable
  legacy quality-check parity remain unproven.
- One existing strict expected-failure test remains for the known duplicate-concurrent-billing
  defect. The suite emits a Pydantic deprecation warning from the installed Inngest dependency.

| Command | Result |
|---|---|
| `uv run --frozen pytest tests/unit/test_gate.py -q --cov=gate --cov-branch --cov-report=term-missing --cov-fail-under=100` | Pass; 15 passed, 100% line and branch coverage for `src/gate`. |
| `bash scripts/check.sh` (using the existing PostgreSQL container through a temporary `psql` bridge) | Pass; migrations and RLS validation succeeded; 207 passed, 1 expected failure; 89.38% project coverage. |
| `uv run --frozen pytest -m eval --collect-only -q` | No eval tests collected; current repository marks no tests as eval. |
| `git diff --check` | Pass. |
