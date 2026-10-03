# Build log

Current phase: 0 (Prove) and 1 (Build), integrated on Google Cloud  
Current work package: job pipeline gap passes on `feat/job-posting-pipeline`  
Last updated: 2026-10-03

## Job pipeline gap passes (2026-10-03, branch `feat/job-posting-pipeline`)

Plan (5 lines): finish spec sections 7 and 9.7 for the jobs WP. Files: `src/jobs/gap.py`,
`pipeline.py` (gap pass), `stage.py` (bounded pass loop), `repo.py` (`job_pipeline_passes`),
`dedupe.py` (canonical-URL keys), migration 0013, `tests/jobs/test_gap_rules.py`,
`test_gap_and_resume.py`, `tests/isolation/test_jobs_isolation.py`; acceptance = remainder-only
passes, run-level caps, resume matrix, DB append-only and RLS; decisions: none new (D-16/D-17 stay
open); [VERIFY] V-JOBS-2 (rollback under concurrent writers).

Built:
- `jobs.gap` (pure): the remainder is the retryable residue only — unescalated because of the
  page-read cap, unclassified under the extractor cap or an isolated error. Verdicts (disagreement,
  unverified, unclear, hostile) and out-of-window postings are final; `progress()` counts a new
  read or a new class and nothing else.
- `JobsPipeline.gap_pass`: re-processes only the remainder, texts re-read from the payload store,
  no source call, inside what is left of the RUN-level caps; exits `converged`, `pending`,
  `budget` or `no_progress`; `stage.run_jobs` bounds it at `max_gap_passes` (2, sourced) and
  persists every pass before the next starts, so a resume continues after the last stored pass.
- Escalation is now an explicit state (`none`, `read`, `failed`, `cap`); a read spent on a posting
  whose processing failed is kept as `failed` so no later pass re-reads it (one read per posting).
- Dedupe keys are the smallest provider id, else the smallest canonical URL; records without a
  canonical URL are rejected and counted, so the weak fingerprint is never a group key.
- Migration 0013 (expand only): `job_pipeline_passes` (insert-only, forced RLS, `TO app_rw`),
  `pass_no` on classifications and sentences, a `(tenant_id, run_id)` unique constraint on
  `job_pipeline_runs`; classifications re-added per pass, never updated.

Commands and results (PostgreSQL 16 + pgvector in compose; test DB `ai_test`):

| Command | Result |
|---|---|
| `pytest tests/jobs -q` | 77 passed |
| `pytest tests/jobs --cov=jobs --cov-config=/dev/null --cov-fail-under=90` | 90.34%; `gap.py`, `counts.py`, `dedupe.py` 100% |
| `pytest tests/isolation -x` | 22 passed (new `test_jobs_isolation.py`) |
| `pytest -m "not eval" -q` | 1167 passed, 15 skipped |
| `ruff check` and `ruff format --check` (touched files and whole repo) | pass |
| `mypy --strict src/jobs`; `mypy` (project) | pass (20 files; 138 files) |
| selftest.sh; validate_assets.py; lint_migrations.py | `selftest OK`; `OK: 33 rows...`; `OK: 24 file(s)` |
| `check_rls_catalog.sql` on the migrated `ai_test` | no rows |
| `alembic downgrade 0012` then `upgrade head` then `check` | both directions ran; no drift |
| lint-imports, interrogate, check_comments, check_guarantees, gen_loops --check, bandit, vulture, compileall | pass |
| mutmut | not run on this change (config extended; last clean-clone numbers stand) |

Not done or open: D-16/D-17 product inputs; V-JOBS-1 (feed text completeness sample) and V-JOBS-2
(staging-data rollback test); mutmut rerun; the tech-stage cross-check has no consumer yet
(`platform_posting_evidence` is exposed but the tech stage does not read it).

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

## Tracker state machine (2026-10-02)

- Plan: Build WP for Tracker state machine (`src/account_intelligence/tracker/`).
- Files touched: `src/account_intelligence/tracker/*`, `migrations/0008_tracker.sql`, `tests/tracker/*`, `tests/isolation/test_rls.py`, `src/account_intelligence/workflow/coverage.py`.
- Acceptance tests: Full coverage of transition logic, derivation, completion, isolation, lying-stage, resume matrix, and rendering.
- Open decisions: Propose D-12 (completion policy) and D-13 (tracker transition rules).
- VERIFY: None right now.

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
| `python .../scripts/lint_migrations.py migrations` | `OK: 14 file(s)` |
| `psql ai_test -f .../scripts/check_rls_catalog.sql` (via `docker compose exec db`) | no rows |
| `alembic check`; downgrade 0006 then upgrade head | no drift; both directions ran |
| `ruff check` on connectors, agents, tests/connectors, tests/isolation, tests/integration, migrations | pass except `migrations/versions/0008_tracker.py` (not this WP) |
| `mypy --strict src/account_intelligence/connectors` | no issues in 26 files |
| `pytest tests/isolation -x` | 15 passed (4 new connector-ledger checks) |
| `pytest tests/connectors --cov` | 207 passed, 12 skipped (`test_verify_*`); connectors 96%; ledger, budget, ratelimit, paginate, security 100% |
| `pytest -m "not eval" --cov` (whole suite, once) | 404 passed, 12 skipped, 1 xfailed (defect 5) |
| same, excluding tests/tracker and tests/workflow (being edited concurrently) | 387 passed; project coverage 84% |

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

## Grading engine (2026-10-02, branch `feat/grading-engine`)

Base: `feat/tracker-state-machine` with `feat/broker-connectors` merged (the tracker's 0008 chains onto 0007). Fixed a committed SyntaxError in `tracker/completion.py` (misplaced import) so `tests/tracker` collects.

Plan (5 lines): grading engine WP (1.6 scope plus G1 to G15); files `src/account_intelligence/grading/`, facade `agents/grading.py`, migration 0009, `config/grading_policy.yaml`, `config/source_registry.yaml`, `docs/grading/`, ADR-0010 to 0014, `tests/grading/`, `evals/`; acceptance = reference parity, F1 to F5 failing-first tests, lying-generator tests for every G-rule, DB rule mirrors as app_rw, kill/replay, isolation; decisions D-01 and D-11 extensions, D-14, D-15 (all proposals); [VERIFY] p_scrape `maxAge: 0` bypasses cache (C12).

Built:
- Pure engine (config-driven; thresholds parsed strictly from `grading.yaml` rule strings) with fixes F1 to F5 (`docs/grading/reference-findings.md`).
- Schema: `ProposedClaim` (no grade fields, `extra='forbid'`), `GradedClaim` validators (G2, G5, G6, G8, G9, G13) whoever constructs it; JSON Schema exported.
- Rules: URL and verbatim-sentence checks, value support, hostile-content drop (G3, G4, G14); primary read computed from the ledger (G5); product precedence (D-01 proposal); Lead rule with ledger-checked queries and bounded loops (G9); judge with one re-ask and an unavailable cap (G12); lineage registry with unknown-lineage and unknown-date caps (G11, D-11, D-14).
- Pipeline `grade_claims` (per-claim isolation, every drop counted, order-independent output hash); single write path `record_graded_fact` (idempotent; DB mirrors confirmed-needs-primary-read, only-confirmed-stated, product grade present, append-only, no fact without evidence at commit); `supersede`, `record_drops`, `record_lead`, `pin_config`.
- Adapters over the connector ledger (sources and fetch records re-parsed from stored payloads; the ledger now stores call arguments); p_scrape now requests a live read (`maxAge: 0`).
- Integration: Workflow `distill_grade` handler and `FactRepo`; evidence gate delegates to `grading.spans` (quotes now verbatim, case preserved); tracker Lead check delegates to `grading.leads`.
- Migration 0009 (expand only): grading columns on `facts`, `fact_evidence`, `grade_events`, `claim_drops`, `lead_verifications`, `lead_verification_queries`, `account_runs.grading_config_hash`. The facts fixture in `tests/conftest.py` now writes a graded fact with evidence (the new invariants require it).

Commands and results (PostgreSQL 16 + pgvector in compose):

| Command | Result |
|---|---|
| `bash .../scripts/selftest.sh` | `selftest OK` |
| `python .../scripts/grading_reference.py --selftest` | `selftest OK` |
| `python .../scripts/validate_assets.py` | `OK: 33 coverage rows, 5 product grades, 8 routes, 10 alert rules` |
| `python .../scripts/lint_migrations.py migrations` | `OK: 16 file(s)` |
| `check_rls_catalog.sql` on the migrated test DB | no rows |
| `alembic check`; downgrade 0008 then upgrade head | no drift; both ran |
| `mypy --strict src/account_intelligence/grading`; `mypy` (project) | no issues (17 and 95 files) |
| `ruff check` on all files this WP touched | pass; whole-repo `ruff check .` fails only in tracker files and `0008` (not this WP) |
| `pytest tests/isolation -x` | 19 passed |
| `pytest tests/grading` with coverage | 163 passed (eval deselected); `src/grading` 99%; engine, product, primary, leads, spans, judge 100% |
| `pytest -m "not eval" --cov` | 573 passed, 12 skipped, 1 xfailed (defect 5); 95.25% |
| `python -m account_intelligence.grading.docs --check` | current |
| mutmut 3.8 on engine, product, primary, leads | 840 of 850 killed; 10 survivors all equivalent (`docs/grading/mutation.md`) |
| calibration harness (`evals/grading_calibration.py`) | Blocked: needs analyst-reviewed golden accounts (synthetic labels only) |

Open: every proposal (ADR-0010 to 0013, `docs/decisions/open-decisions.md`); no distinct judge model is configured, so every A, B and Confirmed is capped (`unjudged`); p_search reads are `cached_copy`, so only job pages read through p_scrape can be Confirmed; source registry has no domain entries; the data-layer plan's rename of `facts` will move migration 0009's constraints.

## Job posting pipeline core (2026-10-02)

Plan (5 lines): build pure job normalization, window, match, sentence, classification and count core; own `src/jobs/`, `tests/jobs/`, job config/docs, and a fail-closed workflow adapter; acceptance = synthetic invariants, Ruff and strict mypy; D-16/D-17 remain open and the provided platform-owner mapping remains unchanged; [VERIFY] normalized connector completeness and PostgreSQL migration behavior.

Built:
- Added immutable `RawPosting`/`Posting` contracts, calendar window logic, URL normalization, stable keying and dedupe, description-only platform matching, verbatim sentence spans, proposal cue classification, row-derived counts, typed ports, extractor output validation, bounded one-read escalation, claim templates, export projection, a pull-once pipeline shell, and a fail-closed `s05_jobs` adapter.
- Added proposal configs for PRD lane names, empty search keywords/aliases, four proposal class cues, unresolved full-text scope, and provisional caps. Added docs and ADR-0008 for D-16/D-17, status/window/PII proposals, and `[VERIFY]` gaps.
- Tests added before implementation. `PYTHONPATH=src uv run pytest tests/jobs -q`: 9 passed. `uv run ruff check src/jobs tests/jobs src/account_intelligence/workflow/stages/s05_jobs.py`: pass. `PYTHONPATH=src uv run mypy --strict src/jobs`: pass, 17 source files.
- Gates: skill self-test, asset validation, and migration lint passed. Job tests: 9 passed; measured jobs package coverage 72% (target 90%, not met). Ruff and strict mypy passed. Isolation suite blocked because the configured local PostgreSQL connection rejected authentication (`fe_sendauth: no password supplied`). Full suite blocked during collection by an existing `SyntaxError` in `src/account_intelligence/tracker/completion.py` (future import is not first). Catalog RLS, mutation testing, resume matrix were not run. No migration or production repository was added.
- Not proven: production idempotency across process crashes, full-text completeness from a real adapter, two-pass extractor/judge integration, per-run loop caps, counts against the full requested oracle corpus, DB RLS, coverage evidence integration. The stage raises `NotConfigured`, so no coverage row is falsely successful.
- Outstanding inputs: lane search keywords and aliases; rubric definitions/cues; full-text scope; sample records for board, feed, page-read; PostgreSQL test DB for expand-only schema and RLS validation. D-06 PII approach remains for legal review.

## Integration on Google Cloud (2026-10-02 to 2026-10-03, branch `feat/integrate-gcp-production`)

Plan (5 lines): audit the 13 items against the six spec prompts; finish what was partial (tracker,
job pipeline, workflow stages, run lifecycle); wire one run path through one wiring point
(`app/container.py`); move the runtime to Google Cloud behind ports (ADR-0017, C11) with Terraform,
image, CI and runbooks; prove it with system tests, static guards W1 to W8 and audit pass 2.

Built (commits on the branch, newest last): spec-compliance audit and single coverage key space;
connector results carry ledger keys; tracker rebuilt (ledger-read proof, owner check, DB tests,
0011); job pipeline rebuilt against a hand-written oracle (0012); run lifecycle with one-transaction
creation and the single settlement (ADR-0016); platform ports and Google Cloud adapters with a
contract suite on emulators; all 13 stages and runner v2 with whole-run tests; container, worker
endpoints, `/readyz` fails closed, Inngest and the starter graph retired; static guards and the
comment sweep; Terraform (three environments), image, CI with WIF; D-18 spike tests; P0 harnesses;
ADRs 0017 to 0020; open items; runbooks; audit pass 2. The two project folders were merged into
`/home/sakib/ABM Generator` (2026-10-03).

Defects found and fixed during the work (each with a test): tracker completion guard never fired;
judge or gate could upgrade a row; unknown result counts counted as zero; any stage could write any
row; events recorded from = to; app_rw could not write events; swept rows could be upgraded; strict
Enum rejected every extractor answer; out-of-window postings were classified; board hits never
merged with feed records; dots inside words ended sentences; the rate limiter spun below clock
resolution; a canary echo was dropped uncounted; the cloud container never built the connector
runtime; SIGTERM replaced uvicorn's handler; two slice deliveries could run one run at once; a
package cycle; bidirectional characters in a regex literal; known defect 5 (idempotency key reuse).

Gates (working folder, 2026-10-03): see `docs/audit/AUDIT.md` pass 2. After the mutation tests were
added: `make lint` pass (ruff, mypy 137 files); `pytest -m "not eval" --cov-fail-under=90`: 1145
passed, 12 skipped, coverage 94.41%.

Clean clone (fresh `git clone` of the branch, fresh venv, fresh database `ai_clone`): every static
gate passed; isolation suite 20 passed; RLS catalog check 0 rows; full suite 1074 passed, 12 skipped,
coverage 94.41% (run before the 71 mutation tests were added).

Mutation check (mutmut 3.8.0, config in `pyproject.toml [tool.mutmut]`, run in the clean clone):
tracker derive, machine, completion; jobs counts, window; 600 mutants. First run: 424 killed, 175
survived. Added `tests/tracker/test_mutation_kills.py` (71 tests: reason oracle over 64 cases,
exact verdict blockers and tally, result fields, severity ordering not alphabetical, window end day).
Final: 562 killed, 1 timeout (detected), 37 survived. Survivors reviewed: error message text only,
dict defaults with identical truthiness (`[]` vs `None`, `False` vs `None`), the `""` default for a
missing error class (any unlisted class maps the same way), the downgrade guard the table makes
unreachable (`pragma: no cover`), and defensive invariants in `jobs.counts` that cannot fail because
the counts are computed from the same rows. No production defect found by this pass.

Not proven (needs the owner): anything on real Google Cloud (no apply, no gcloud create, no real
provider or LLM call was made); every VERIFY item in `docs/open-items.md`; the sign-offs for D-01
ext to D-20.
