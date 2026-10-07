# ADR-0057: Usage events and atomic plan limits (D-67)

- Status: Proposed
- Date: 2026-10-07
- Deciders: TODO (product owner)

## Context

The roadmap asks for usage events ready for billing and plan limits; the sources give no values.

## Decision

* Insert-only `usage_events` (`account_added`, `run_started`, `run_settled`, `refresh_run`,
  `export_created`) with a unique `kind:subject` key, written in the action's transaction;
  settlement emits `run_settled` once whatever it replays.
* Plan limits are platform rows seeded from `config/plans.yaml` (proposal values); a tenant's plan
  and quota overrides are insert-only versions. Checks are one conditional
  `UPDATE usage_counters SET used = used + 1 WHERE used + 1 <= limit` (derived, documented,
  recomputable); a soft threshold (80%, proposal) notifies once per period.
* A reconcile job compares the stream with runs, the ledger, accounts and members.
* "Active account" = an account with a run or refresh in the period (proposal).

## Consequences

Proven by `tests/phase3/test_ws7_metering.py` (100 racing requests never exceed the limit,
idempotency, planted drift caught, units only). Values need the product owner.
