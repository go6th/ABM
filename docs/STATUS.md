# Project status

## The 13 roadmap items (audit pass 1, 2026-10-02, branch `feat/integrate-gcp-production`)

"Substitute evidence" = the work package's own committed tests, used because its spec prompt is not yet
in `docs/prompts/` (see `docs/prompts/README.md`).

| # | Item | Status | Evidence | Missing / Blocked |
|---|---|---|---|---|
| P0-1 | Workflow engine decision | Partial | connector kill/resume and two-worker tests (`tests/connectors/test_kill_resume.py`, `test_ledger_budget.py::test_two_workers_on_one_run_never_both_pay`) | no orchestration spike on the target scheduler; no D-18 ADR; sign-off Blocked: needs Tech lead |
| P0-2 | Model split benchmark | Partial | `config/models.yaml` (profiles, arms, no model IDs in `src/`: grep) | no benchmark harness, no results table; run Blocked: needs September account outputs |
| P0-3 | People source decision | Missing | none | adapter switch and comparison protocol; Blocked: needs real accounts, credits, analyst |
| P0-4 | Golden accounts | Partial | `evals/grading_calibration.py` (claim-level, synthetic) | golden-account format and loader; Blocked: needs 3 to 5 analyst-reviewed runs |
| P0-5 | Checklist to config | Partial | `config/checklist.yaml`, `grading.yaml`, `output_spec.yaml`; `validate_assets.py` OK; `tests/unit/test_enums.py` | no test that loads and validates `output_spec.yaml` and the checklist through one typed loader |
| P0-6 | Environments and CI | Missing | `.github/workflows/ci.yml` (GitHub, local Postgres service) | GCP as code, three environments, WIF, deploy; apply Blocked: needs GCP project |
| P0-7 | Data protection review | Missing | none | `docs/data-handling-people-data.md`; sign-off Blocked: needs counsel |
| P1-1 | Data layer v1 | Partial | migrations 0001 to 0009; `tests/db`, `tests/isolation` (19 passed); RLS catalog clean | `job_postings`, `people`, `tech_detections`; two run tables unlinked (AUDIT F-2); payload store has no cloud adapter |
| P1-2 | Broker connectors | Done (p_people Blocked) | `pytest tests/connectors` 207 passed; spec gates in BUILD_LOG | p_people Blocked: needs workspace grid contract |
| P1-3 | Workflow v1 | Partial (substitute evidence) | `tests/workflow` | 13 stage handlers `NotConfigured`; the runner executes the starter graph (AUDIT F-1) |
| P1-4 | Tracker state machine | Partial (substitute evidence); **defect** | `tests/tracker` (pure logic) | completion guard never fires (AUDIT F-3, reproduced); no DB tests |
| P1-5 | Grading engine | Done | `pytest tests/grading` 163 passed; DB refuses Confirmed without primary read (`test_db_refuses_confirmed_without_primary_read_for_app_rw`) | proposals need sign-off (D-01 ext, D-11 ext, D-14, D-15) |
| P1-6 | Job posting pipeline | Partial (substitute evidence) | `tests/jobs` (9 tests) | no `job_postings` table or repo; ports unwired; no counts-equal-oracle test found |

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
