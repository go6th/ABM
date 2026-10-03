# ADR-0016: one run, two rows; creation in one transaction; the settlement rule

- Status: Proposed (the settlement rule is a D-04 proposal)
- Date: 2026-10-03
- Deciders: TODO (product owner for D-04)

## Context

The code base had two unlinked run tables (audit F-2): `account_runs` (orchestration, money,
ledger) and `runs` (data-layer lifecycle; tracker and source foreign keys). The run-creation
route reserved credits but never seeded a tracker, never wrote a `runs` row, and accepted a reused
idempotency key with a different body (known defect 5). No settlement code existed.

## Decision

1. A run is one id with two rows, both written in the same transaction by
   `runs.lifecycle.create_run`, together with the account upsert, the credit reservation and its
   ledger entry, and the seeded tracker. No foreign key links the two rows (existing fixtures
   create `runs` rows alone); the lifecycle module is the only writer of both.
2. `request_hash` (SHA-256 of the canonical body) is stored; reusing a key with another body
   raises `IdempotencyConflict` (HTTP 409).
3. Transitions use the pure table in `workflow/transitions.py` with optimistic updates; terminal
   ends go only through `lifecycle.finish`, which sweeps, calls `tracker.guard.finish_run`, then
   settles. `runs.status` maps per ADR-0015.
4. **Settlement (D-04 proposal):** commit `reserved_credits * min(1, spent_usd / budget_usd)`,
   refund the rest; a FAILED run (platform fault) or an approval timeout refunds everything. One
   commit and one refund entry per run (partial unique indexes from 0010, row lock, `ON CONFLICT DO
   NOTHING`), so `reserved = committed + refunded` and a replay is a no-op. The credit reserve per
   depth (6 and 18) is carried over from the starter, marked "calibrate with data"
   (`config/billing.yaml`).

## Consequences

- No price is invented: dollars (`spent_usd`) and credits (`reserved_credits`) stay separate
  (known defect 4); the commit is a share of the agreed reservation.
- Evidence: `tests/runs/test_lifecycle_db.py`.
