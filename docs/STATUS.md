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
| P1-7 | People pipeline (branch `feat/people-pipeline`) | Done hermetically; providers Blocked | `tests/people` (oracle, lying tests per P-rule, properties, resume matrix, DB), `tests/isolation/test_people_isolation.py` | D-21 to D-23; V-PEOPLE-1 to 3; OI-PPL-2 to 5 |

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

Status: partial core on branch `feat/job-posting-pipeline`. Deterministic pure logic and
synthetic tests are available in `src/jobs/` and `tests/jobs/`. The workflow adapter fails
closed with `NotConfigured`; posting persistence, coverage evidence integration, and
production connector composition are not implemented. No posting-table migration was
added because PostgreSQL behavior could not be checked against a test database.

D-16 and D-17 are open in ADR-0008. Search keywords and aliases are empty; full-text scope
is unselected. Connector completeness and posting response shapes remain `[VERIFY]` until
recorded samples are supplied. Job test result: 9 passed; measured package coverage 72%, below the requested target. Ruff and strict mypy pass. Isolation was blocked by local PostgreSQL authentication; the full suite failed collection on a pre-existing tracker syntax error. No DB or RLS claim is made.

See `BUILD_LOG.md` and `docs/jobs/` for scope and gate results.
