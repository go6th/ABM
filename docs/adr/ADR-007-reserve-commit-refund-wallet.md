# ADR-007 — Reserve / commit / refund wallet ledger

Date: 2026-10-01
Status: Accepted

## Context
Post-hoc debits from telemetry allow overspend under concurrency: N parallel run creations
each observe a sufficient balance and all proceed (v1 defect).

## Decision
Credits are reserved atomically at run creation (single conditional `UPDATE wallets SET
balance = balance - est WHERE balance >= est` inside the same transaction as the run row),
committed at the terminal state for actual cost, refunded for the remainder. `wallet_ledger`
is insert-only and the wallet row is derived; a nightly job verifies balance = ledger sum.

## Consequences
No overspend, no double charge, full audit trail; slightly more write traffic per run.

## Revisit if
Reservation contention on hot wallets becomes measurable at scale — then shard wallet rows
by tenant with the same invariants.
