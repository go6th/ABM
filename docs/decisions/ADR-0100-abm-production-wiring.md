# ADR-0100: ABM agent layer on the production platform

- Status: Proposed
- Date: 2026-10-08
- Deciders: TODO (product owner)

## Context

The agent layer (ADR-0091 to ADR-0099) ran on ports with test fakes. Production needs each port
bound to the existing component without a second implementation of any rule, and a way to run
the ABM graph without replacing Workflow v1 for every tenant (C34 is the owner's decision).

## Decision

- Ports bind to existing components (`src/agents/production/`): provider calls through
  `connectors.router.call_need` (routing, pull once, credit gate); payloads in the platform store
  under neutral keys; facts through `grade_claims` and `record_graded_fact`; rows through
  `TrackerService` as the owning stage; the cap proposed and auto-approved through
  `spend.service` with an ABM pull plan; approvals as credit pause requests; files, gate and the
  delivery note through the existing builders; settlement stays in `lifecycle.finish`.
- The grading engine gains `match_origin` and the precedence rule `unread_match` (C36): the one
  grader enforces "a keyword match or date range is a Lead until read".
- A Phase 2 run reads its declared parent pull's ledger as its own inputs (C35).
- `workflow.runner.execute_slice` takes an optional graph factory: the ABM graph runs under the
  same slice lifecycle. The worker endpoint uses it for Phase 1 only and Phase 2 only runs, and
  for full runs only when the tenant's `abm_agents` flag is on (deny by default).
- Terminal mapping: completed is COMPLETED; a pull run is PARTIAL `pull_only` (C37); a pre-spend
  failure (no pulled data, stale pulls, invalid setup) is FAILED with a full refund; anything else
  is PARTIAL with the reason.

## Consequences

- Not bound yet (fail closed or switched off): people recording from the agent layer
  (`NotConfigured`, counted `people_not_recorded`), the internal-history connector search (D-98:
  off), the Internal Only file builder (the internal audience builds no file).
- Most coverage rows have no grading attribute class (C38): their claims are refused and counted
  until the owner adds classes with half-lives.
- Evidence: `tests/system/test_abm_production.py` (whole runs on Postgres),
  `tests/agents/test_production_adapters.py`, `tests/grading/test_unread_match.py`.
