# Project status

## The 13 roadmap items (audit pass 2, 2026-10-03, branch `feat/integrate-gcp-production`)

"Blocked" means the code and its harness exist and the remaining step needs something only the
owner can supply (named in the last column). Evidence is a test or command run on this branch.

| # | Item | Status | Evidence | Remaining / Blocked |
|---|---|---|---|---|
| P0-1 | Workflow engine decision (D-18) | Done hermetically; sign-off Blocked | ADR-0018; `tests/system/test_kill_resume.py` (5 kills, two workers); approval and timeout tests | real-queue spike (V-TASKS-1); tech lead sign-off |
| P0-2 | Model split benchmark | Harness done; run Blocked | `evals/model_benchmark.py`, `docs/benchmarks/model-split.md`, `tests/unit/test_evals_harnesses.py` | September outputs and reviewed golden accounts |
| P0-3 | People source decision | Switch and protocol done; decision Blocked | `config/people_sources.yaml`, `evals/people_compare.py`, `docs/people-source-comparison.md` | real accounts, credits, analyst, p_people contract |
| P0-4 | Golden accounts | Format and loader done; content Blocked | `evals/golden_accounts.py`, `evals/golden/accounts/synthetic-acme.yaml` | 3 to 5 analyst-reviewed accounts |
| P0-5 | Checklist and output spec to config | Done | `config_files.load_all`, `tests/unit/test_config_files.py`, `tests/workflow/test_stage_map.py` | |
| P0-6 | Environments and CI | Done as code; apply Blocked | `infra/` (fmt and validate pass), `Dockerfile` (smoke), `.github/workflows/ci.yml` (actionlint), `make deploy-staging`, `docs/runbooks/` | owner's GCP projects, billing, WIF variables |
| P0-7 | Data protection review | Draft done; review Blocked | `docs/data-handling-people-data.md` (Draft for counsel) | counsel |
| P1-1 | Data layer v1 | Done | migrations 0001 to 0012; `tests/db`, `tests/isolation` 20 passed; RLS catalog clean | |
| P1-2 | Broker connectors | Done (p_people Blocked) | `tests/connectors` | p_people grid contract |
| P1-3 | Workflow v1 | Done (gate `NotConfigured`; builders wired, Node renderer `NotConfigured` in production) | `tests/system/test_whole_run.py`, `test_failure_injection.py`, `tests/workflow` | deliverable builders (OI-BLD-1) |
| P1-4 | Tracker state machine | Done | `tests/tracker` (lying stages, DB defences, races, SIGKILL, stateful) | D-12, D-13 sign-off |
| P1-5 | Grading engine | Done | `tests/grading`; lying-generator whole run | D-01 ext, D-11 ext, D-14, D-15 sign-off |
| P1-6 | Job posting pipeline | Done (deviations listed) | `tests/jobs` (oracle, lying model, properties, DB) | D-16, D-17; OI-JOBS-1 to 3 |
| P1-7 | People pipeline (branch `feat/people-pipeline`) | Done hermetically; providers Blocked | `tests/people` (oracle, lying tests per P-rule, properties, resume matrix, DB), `tests/isolation/test_people_isolation.py` | D-21 to D-23; V-PEOPLE-1 to 3; OI-PPL-2 to 5 |
| P1-9 | Credit cap v1 (branch `feat/credit-cap`) | Done hermetically and on Postgres; prices and balances Blocked | `tests/spend` (hand oracles, lying input per K-rule, properties, kill and resume, fault injection), `tests/isolation/test_spend_isolation.py`, `tests/system/test_credit_cap_run.py`, ADR-0030 to 0035 | D-27 to D-31; provider cost table and credit rates (V7); balance endpoints (V-SPEND-1); approver roles, timeouts, limits |
| P1-8 | Export builders, WP 1.8 builder half (branch `feat/export-builders`) | Done hermetically and on Postgres with a neutral proposal look; legacy port and parity Blocked | `tests/exports` (hand-written oracle, lying content per B-rule, properties, same look, byte-identical rebuilds, LibreOffice PDF and recalculation checks, Postgres end to end), `tests/isolation/test_exports_isolation.py`, ADR-0026 to 0029 | legacy Node library, workbook scripts, one real delivered file set; D-24, D-25, D-26; OI-EXP-1 (distill writes `report_sections`), OI-EXP-2/3 (source registry for posting and post links) |
| P2-PAR | Parallel execution and credit manager v2 (branch `feat/parallel-credit-v2`) | Done hermetically and on PostgreSQL 16; opt-in (`execution_mode: parallel`); the 4 h target Blocked | `tests/parallel` (DAG and PRD edges, property tests of the scheduler, merge, critical path and governor, SQL/Python governor parity, 100 workers over 50 runs never above the limit, executor resume, failure isolation, 50 random completion orders, oracles, simulation), `tests/spend/test_v2_*`, `tests/system/test_parallel_run.py`, `test_parallel_cap_pause.py`, `tests/api/test_spend_v2_routes.py`; ADR-0037 to 0041 | D-42 to D-47 sign-off; real provider limits and measured durations (`docs/parallel/VALIDATION.md`); upload store, scanner and parser adapters (V-UPL-1, V-UPL-2); the operator plane (D-33); V-GOV-1 |
| P2-AI | Model routing, verifier, eval harness (branch `feat/ai-routing-verify-eval`) | Done hermetically and on PostgreSQL 16; router opt-in and refused without a benchmark; benchmark and real scores Blocked | `tests/llm` (selection properties, fallback classes, data classes, independence, schema re-ask, budget, prefix bytes, SDK error mapping, call-site scan, Postgres replay), `tests/verify` (blind re-read oracles, lying verifiers, downgrade-only property, real Confirmed facts superseded on Postgres), `tests/evals` (metric and statistics known answers, A/A bound, seeded regressions, golden, contamination, security, baselines, replay eval end to end); ADR-0042 to 0045 | D-48 to D-51; C20 (one model family); analyst-labelled golden accounts; model access, prices and terms (V-LLM-4) |
| P3 | Multi-tenant product (branch `feat/phase3-tenant-product`) | Done hermetically on PostgreSQL 16 with fakes; pilots, real IdPs, real connectors and the penetration test Blocked | `tests/phase3`, `tests/isolation` (generated matrix), `web/e2e` (Playwright), `docs/phase3/EXIT_CRITERIA.md`; ADR-0051 to 0060 | D-61 to D-70; C23 to C26; V-P3-*; OI-P3-1 to 7 |
| P4 | Automation and signals (branch `feat/phase4-automation`) | Done hermetically on PostgreSQL 16 with fakes and a 26-week simulation; real monitors, Slack, CRM, pilots, the ratio and the real DR drill Blocked | `tests/phase4`, `ops/dr/restore_drill.py`, `docs/phase4/EXIT_CRITERIA.md`; ADR-0061 to 0070 | D-71 to D-80; C27 to C29; V-P4-*; OI-P4-1 to 7 |
| P4-OBS | Observability (branch `feat/observability`) | Partial: contract, scrub, trace identity, deterministic span ids, root span at settlement, API metrics, timings, cost rollups and reconciliation, SLO and burn math, alert and dashboard generation, stuck detector, isolation prober, release verdict, ops routes (fail closed), runbooks with tested queries are Done; stage, unit and call spans in the engine, executor and connectors, `traceparent` propagation, the console pages (no `web/`) and production exporters are not done | `tests/obs` (contract checks, scrub property, trace identity property, burn math property, every alert fires and stays quiet, chaos: telemetry failures never reach the caller, span scrubbing, resume span ids, label dropping, log allow-list, Postgres: cost rollup exact and reconciled, stuck scan, prober finds a broken policy, timelines, runbook queries, ops routes); ADR-0046 to 0050 | D-56 to D-60; C22; V-OBS-1, V-OBS-2; on-call tool, channels, retention, project ids |

## Parallel execution and credit manager v2 (2026-10-06)

Status: built on branch `feat/parallel-credit-v2` (from `main`). The research stages run as units
on a dependency-aware scheduler (`src/parallel/`) with per-provider Postgres leases across all runs
(every ceiling 1 until measured), a key-ordered merge, typed unit outcomes recorded in
`work_unit_events`, and a pause for approval instead of failing at the cap (`src/spend/` v2:
soft and hard limits, live spend, pause and step-up approval, the exhaustion ladder, the operator
upload step, the notification outbox). The default execution mode stays `sequential` until the
decisions are signed; every provider call in production now holds a governor slot.

Proven by tests: dependencies respected and every PRD edge pinned; concurrency never above the limit
(continuous monitor, 100 workers, 50 runs, two tenants); identical merged output under 50 random
completion orders; the parallel whole run ends with the same 33 row statuses as the sequential one;
one pause request per cap version under races; every bad approval refused; approval appends a cap
version; decline and timeout end BUDGET_EXCEEDED with partial results and one settlement; the
ladder order; hostile uploads rejected with typed reasons and an unavailable scanner failing closed;
no amount or provider in tenant views and notifications.

Not proven: the 4 hour target on real accounts (Blocked, `docs/parallel/VALIDATION.md`), real
provider limits, real notification delivery, a real scanner and upload store, Cloud Tasks
behaviour, `governor_definer` ownership with a non-superuser migrator (V-GOV-1).

## Earlier work-package notes

Updated 2026-10-02.

## Workflow v1

Status: partial scaffold. `config/workflow_v1.yaml` defines the 13 research stages
and dependencies; the core contracts and deterministic tests are in
`src/account_intelligence/workflow/` and `tests/workflow/`.

### Implemented and locally verified

- Workflow specification parsing, cycle/ownership checks, and sequential LangGraph
  construction.
- Small checkpoint state, starter-shape migration checks, and reducers for references,
  counters, completion markers, and loop outcomes.
- Coverage statuses with required proof, lifecycle transition table, and evidence
  source/quote/URL/figure checks.
- Workflow-specific Ruff and strict mypy; seven workflow tests; skill asset validation;
  migration lint.

### Not implemented

Production stage handlers, data repositories, provider and model adapters, persisted
workflow checkpoints for this graph, end-to-end slice runner, production builder and
gate adapters, and wallet settlement. Missing adapters must not be represented as
successful work; the stage base raises `NotConfigured`.

### Decisions and source gaps

- P2.15 owner `pass1_relationship` confirmed by the user on 2026-10-02.
- Original PRD Appendix A is absent from `docs/sources/`; all other inferred checklist
  ownership is provisional and annotated in `docs/workflow/stage-map.md`.
- Soft budget shares and unsourced source-specific loop caps are provisional config.
- Wallet reserve is in credits while the workflow budget is USD. D-04 must settle the
  wallet accounting unit and conversion before finalization can be implemented safely.
- A distinct judge profile remains TODO because the active model config uses one
  profile for synthesis and verification.
- Postgres-backed isolation, settlement, replay guarantees and full integration remain
  unverified; the local PostgreSQL service was unavailable.

See `BUILD_LOG.md` for command results and prior work packages.

## Broker connectors (WP 1.4)

Status: built on branch `feat/broker-connectors`, not merged. Code in
`src/account_intelligence/connectors/`, docs in `docs/providers/`, decisions ADR-0006
(lease and ledger) and ADR-0007 (retry owner).

| Provider | Operations | Status |
|---|---|---|
| p_search | web_search, read_page | Done |
| p_scrape | scrape_job_page (plain) | Done |
| p_signals | news_events, job_openings, tech_detections | Done |
| p_org | org_resolve, org_tech_stack, people_by_technology | Done; org_teams not specified |
| p_people | people_by_title, profile_enrichment | Blocked: needs the workspace grid contract |

Verified locally against PostgreSQL: isolation suite, RLS catalog, kill/resume in a
subprocess, two workers, 50 concurrent calls, budget caps, spend equals ledger. Open
items: `docs/providers/README.md`.

## Grading engine

Status: built on branch `feat/grading-engine` (stacked on the tracker and connector branches), not merged.
Code in `src/account_intelligence/grading/` with the public facade `agents/grading.py`; rules in
`docs/grading/rules.md` (generated); decisions ADR-0010 to ADR-0014 and `docs/decisions/open-decisions.md`.

Proven by tests: no model-set grade; Confirmed only from a code-verified live T1 page read in this run
(also refused by the database for the runtime role); no Lead finalised without three ledger-backed,
differently worded queries across two categories; no fact without a URL and a verbatim sentence (or a
search proof for absence), and none without evidence at commit; deterministic output.

Needs decisions: D-01 and D-11 extensions, D-14 (dating and read origin), D-15 (source registry),
the similarity threshold, and a judge model distinct from the generator.

## Job posting pipeline

Status: built on branch `feat/job-posting-pipeline` (rebuild plus the gap-pass work package,
2026-10-03). `src/jobs/` holds the pure pipeline, the bounded gap passes (`jobs.gap`, at most
`max_gap_passes` = 2 passes over the remainder, no re-pull, run-level caps), the fail-closed
workflow adapter, and the Postgres repo; migration 0013 adds the append-only
`job_pipeline_passes` table and the `pass_no` columns (expand only, forced RLS).

Proven by tests (`tests/jobs`, 77 passed on the branch): counts equal the hand-written oracle with
the invariants; every J-rule has a lying-model test; the gap pass re-processes only the remainder
and never re-pulls; the resume matrix (board, feed, mid-batch, after persist, after a gap persist)
resumes to the same rows hash with no call paid twice; the DB round trip, append-only refusals and
tenant isolation are tested against real Postgres. Package coverage 90.34% (target 90).

See `BUILD_LOG.md` and `docs/jobs/` for scope and gate results.

## Credit cap v1

Status: built on branch `feat/credit-cap` (2026-10-04). One cap per run in USD (integer
micro-dollars), proposed in code after a free balance preflight, approved by a person bound to its
hash (or a tenant policy within its limit), enforced inside the connector's ledger claim before
every paid call, spend derived from the insert-only ledger, and an internal report stored once per
terminal state in the finishing transaction; the tenant sees categories and call counts only.

Proven by tests: no paid call without an approved cap (directly at the connector); the cap is never
exceeded under 200 parallel calls, two workers or ten runs over two tenants, and `spent <= cap +
overshoot` after any reserve and true-up sequence; unknown cost is never zero; integer money;
derived spend reconciles or the report is stamped; replays cost nothing; one settlement and one
report per terminal run, across kills, racing finalisations and duplicate deliveries; no spend in
client output. Coverage 99% (100% on money, gate, priority, approval, report, reconcile).

Not proven: real prices, credit rates and balance endpoints; Cloud SQL concurrency; the operator
console and notifier.

Needs decisions: D-16 (class definitions and full-text scope) and D-17 (lane keywords and aliases)
are open in ADR-0008. Open items OI-JOBS-1 and OI-JOBS-2 remain; OI-JOBS-3 (gap passes) is closed.
`[VERIFY]`: connector completeness and posting response shapes (recorded samples); V-JOBS-2, the
0013 rollback under concurrent writers, untested against staging data.

## Export builders (WP 1.8, builder half)

Status: built on branch `feat/export-builders` (people pipeline merged in, 2026-10-04).
`src/exports/` reads every row of a run from one `REPEATABLE READ, READ ONLY` snapshot, maps it to
render models (pure), renders the Account Summary and Sources and Evidence (Word, sandboxed
subprocess behind a JSON contract) and the People List, Job Postings and Tech Stack and Teams
(openpyxl), verifies each file from its bytes (leak scan, row keys re-read), builds the workspace
zip and stores the set append-only and versioned (migration 0016).

Proven by tests: rows in equal rows out (re-read from the produced files, and in the PDF when
LibreOffice is installed); one snapshot under a concurrent writer; byte-identical rebuilds; no
model, network or clock in the builders (static test); every number registered with its base;
no orphan citation; no leak (denylist, provider codes, internal hashes, other accounts, guarded
people names) in any member of any file; formula-like text stored as text; long text continued and
counted; safe zip; a failed build stores nothing; a repeat build writes nothing; a redelivery
supersedes. Exports coverage 97% (100% on policy, numbers, citations, fingerprint, change note,
workspace); mutation 625 of 667 killed, survivors reviewed (`docs/exports/mutation.md`).

Not proven: the real look and legacy parity (the legacy files were not supplied; the theme is a
labelled proposal); the full quality gate (next work package, behind `GatePort`); render checks in
the production image (no LibreOffice there, V-EXP-2). Production builds end PARTIAL until the
Node renderer is ported and OI-EXP-1 to 3 are closed.
