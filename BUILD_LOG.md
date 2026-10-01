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
