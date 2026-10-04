# ADR-0030: credit cap table naming: account_runs carries the enforced cap

- Status: Proposed
- Date: 2026-10-04
- Deciders: TODO (tech lead)

## Context

The data layer defined `runs.credit_cap` (integer, migration 0004) while the starter, the
connector reserve (`budget.reserve`), settlement (`lifecycle.settle_once`) and the workflow all use
`account_runs.budget_usd` and `spent_usd` (ADR-0016: one run, two rows). The spec forbids a third
cap column.

## Decision

1. The enforced cap is `account_runs.budget_usd` (USD, D-27): the approved cap version is written
   there by one conditional UPDATE (`spend.repo.apply_cap`), with `account_runs.cap_version`
   naming the `run_cap_versions` row it came from. The connector's atomic
   `UPDATE ... WHERE spent_usd + :est <= budget_usd` stays the only writer of spend.
2. `runs.credit_cap` is the data layer's mirror of the agreed figure and is not used for
   enforcement; nothing is dropped or altered.
3. Migration 0015 adds, expand-only: `cap_required` (true for rows created after it: no paid call
   without an approved cap; older rows keep the legacy budget rule), `cap_version`,
   `cap_sample_ops`, `attended`, `cap_overshoot_usd`, `unreconciled_usd`, and
   `CHECK (spent_usd <= budget_usd + cap_overshoot_usd)` added NOT VALID then validated; the
   insert-only `run_cap_versions`, `run_cap_events`, `balance_snapshots`, `spend_reports`,
   `prepulled_files`, `spend_defects`; the view `run_spend_by_provider`; and widens the ledger's
   `cost_source` CHECKs with `prepulled` (D-31). It is numbered 0015 after 0014 (people).

## Consequences

- K1 (one active cap) is checked at finalisation and nightly: the run's columns must equal the
  latest approved version (`spend.reconcile`).
- Evidence: `tests/spend/test_db.py`, `tests/isolation/test_spend_isolation.py`.
