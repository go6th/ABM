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

## WP 1.4 broker connector layer (2026-10-02, branch `feat/broker-connectors`)

Plan (5 lines): WP 1.4 connectors behind one interface; files `src/account_intelligence/connectors/` (the repo's package root, adapted from the brief's `src/connectors/` as the data layer did), migration 0007, `config/providers.yaml`, `docs/providers/`, ADR-0006/0007, `tests/connectors/`; acceptance = contract/golden, kill/resume, two workers, 50 concurrent calls, budget caps, SSRF, leak scan, defect-3 xfail removed; decisions touched D-03 (both people providers kept), D-11 (lineage only when the provider states it); [VERIFY] V7 broker prices and limits.

Built:
- Provider facts sourced from first-party docs fetched 2026-10-02 (`docs/providers/sources/`, SHA-256 in `INDEX.md`); five spec files and `docs/providers/CLAIMS.md`. Nothing taken from memory.
- Connectors: p_search (web_search, read_page), p_scrape (plain scrape_job_page), p_signals (news, jobs, tech feeds, paged), p_org (org_resolve, org_tech_stack, people_by_technology async start+poll). p_people: transport sourced but both operations raise `NotSupportedYet` (grid output is workspace-defined; see `docs/providers/p_people.md`). p_org org_teams not specified.
- Ledger v2 (defect 3): `run_leases` lease row plus insert-only `tool_call_events` intent log, budget reservation in the same transaction as the intent, true-up with the terminal event; stale attempts abandoned and re-issued once with `possible_duplicate`; async jobs resumed by polling. `agents/graph.py` `ledgered_call` now runs on it; `test_concurrent_identical_calls_pay_once` xfail removed.
- Budget columns on `account_runs` (`budget_usd`, `spent_usd`, `tool_calls_used`, `tool_call_cap`); credits and dollars never share a column (defect 4); unknown dollar cost charges a configured reserve and is flagged, never 0.
- Errors, retry: connector classifies and paces (shared cooldown), node `RetryPolicy` is the only retry owner (ADR-0007); `fetch_branch` maps final failures to Blocked coverage and the run to PARTIAL.
- Router with playbook routes and fallbacks; builtin fallbacks treated as unavailable (open item); sink guard for hosted files and p_org to client.
- Security: https only, no userinfo, private/loopback/link-local/metadata refused, DNS checked, page hosts allow-listed, provider-reported redirects re-checked; secrets wrapped, redacted, never stored.
- Deviations: the connector protocol is `build_request`/`parse` instead of `execute`/`normalise` so the pipeline owns HTTP and can ledger each page; the breaker is checked after the claim so replays work while a circuit is open.

Commands and results (PostgreSQL 16 + pgvector in compose, Redis 7):

| Command | Result |
|---|---|
| `bash .claude/skills/account-intelligence-platform/scripts/selftest.sh` | `selftest OK` |
| `python .../scripts/validate_assets.py` | `OK: 33 coverage rows, 5 product grades, 8 routes, 10 alert rules` |
| `python .../scripts/lint_migrations.py migrations` | `OK: 12 file(s)` |
| `psql ai_test -f .../scripts/check_rls_catalog.sql` (via `docker compose exec db`) | no rows |
| `alembic check`; downgrade 0006 then upgrade head | no drift; both directions ran |
| `ruff check .`, `ruff format --check .`, `mypy` (project) on this branch | pass |
| `mypy --strict src/account_intelligence/connectors` | no issues in 26 files |
| `pytest tests/isolation -x` | 15 passed (4 new connector-ledger checks) |
| `pytest tests/connectors --cov` | 207 passed, 12 skipped (`test_verify_*`); connectors 96%; ledger, budget, ratelimit, paginate, security 100% |
| `pytest -m "not eval" --cov` on this branch alone (clean worktree) | 387 passed, 12 skipped, 1 xfailed (defect 5); project coverage 94.62% |
| `mutmut` | not run |

Not done or open: p_people operations (need grid contract), dollar prices per credit for p_scrape/p_signals/p_org, plan rate limits for p_scrape/p_signals, builtin fallback decision, runner should `release_lease` at slice end (WP 1.5), live smoke test not written (no credentials; nothing to run against).
