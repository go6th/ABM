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
| P1-3 | Workflow v1 | Done (builders and gate `NotConfigured`) | `tests/system/test_whole_run.py`, `test_failure_injection.py`, `tests/workflow` | deliverable builders (OI-BLD-1) |
| P1-4 | Tracker state machine | Done | `tests/tracker` (lying stages, DB defences, races, SIGKILL, stateful) | D-12, D-13 sign-off |
| P1-5 | Grading engine | Done | `tests/grading`; lying-generator whole run | D-01 ext, D-11 ext, D-14, D-15 sign-off |
| P1-6 | Job posting pipeline | Done (deviations listed) | `tests/jobs` (oracle, lying model, properties, DB) | D-16, D-17; OI-JOBS-1 to 3 |

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

Needs decisions: D-16 (class definitions and full-text scope) and D-17 (lane keywords and aliases)
are open in ADR-0008. Open items OI-JOBS-1 and OI-JOBS-2 remain; OI-JOBS-3 (gap passes) is closed.
`[VERIFY]`: connector completeness and posting response shapes (recorded samples); V-JOBS-2, the
0013 rollback under concurrent writers, untested against staging data.
