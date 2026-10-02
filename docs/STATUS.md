# Project status

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
