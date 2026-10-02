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

## Workflow v1 scaffold (2026-10-02)

- Added a validated YAML DAG, sequential LangGraph builder, typed stage/repository ports, small checkpoint state and schema migration, proof-required coverage tracker, deterministic evidence checks, and optimistic lifecycle transition rules.
- Added a total checklist-ID ownership map. P2.15 (`customers_partners_matched`) is assigned to `pass1_relationship` per product-owner response on 2026-10-02. Other ownership inferred from labels and process descriptions is identified as proposed because original PRD Appendix A is absent.
- Added ADR-0005 to record PRD step 9 before step 10, with the Architecture judge pass inside `distill_grade`.
- Production stage adapters, persistence repositories, builder, gate, workflow runner, and wallet settlement remain unimplemented; their ports fail closed with `NotConfigured`. Wallet settlement is blocked on the credits/USD distinction (D-04); no unit conversion was invented.
- Workflow component harness: `pytest tests/workflow -q --cov=account_intelligence.workflow --cov-config=/dev/null --cov-fail-under=90` — 11 passed, 100% statement coverage in `src/account_intelligence/workflow/`. `ruff check src/account_intelligence/workflow tests/workflow` — pass. `mypy --strict src/account_intelligence/workflow` — pass.
- `validate_assets.py` — pass (33 rows); `lint_migrations.py migrations` — pass (12 files).
- `ruff check .` — blocked by 11 existing E501 violations in `tests/connectors/`, untouched by this work.
- `pytest tests/isolation -x` and `pytest -m "not eval" -q` — blocked at the shared database fixture because PostgreSQL is unavailable at localhost:5432. No RLS or database durability claim is made.
- `bash .claude/skills/account-intelligence-platform/scripts/selftest.sh` — pass (`selftest OK`).
- Not run: catalog RLS query (`psql` is unavailable), full adversarial FakeLlm matrix, whole-run/resume matrix, settlement property/concurrency harness, and Inngest timeout path.
