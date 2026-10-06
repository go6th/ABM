# ADR-0038: per-provider concurrency governor on Postgres leases (D-43)

- Status: Proposed (D-43)
- Date: 2026-10-06
- Deciders: TODO

## Context

ARCH: the real limits are broker and LLM rate limits and Postgres connections, so concurrency is
capped per tenant and per broker; ARCH names a Redis token bucket, but the GCP decision (ADR-0017,
C11) keeps Redis only for pacing. The sources give no provider's concurrency limit.

## Decision

* `provider_leases` and `provider_waiters` (tenant-scoped rows, forced RLS) and the global
  `provider_limits` row per provider. All writes go through `governor_*` SECURITY DEFINER
  functions owned by the NOLOGIN role `governor_definer`, whose policy sees every tenant's leases;
  the functions take the tenant from `app_tenant()` and return only a lease id, a boolean or a
  count. A provider-row lock serialises acquisitions across workers.
* Rule (one implementation in Python `parallel.governor.choose`, mirrored in SQL and proven equal by
  a parity property test): no grant at or above the current limit; run and tenant share caps; the
  slot goes to the waiter whose tenant holds fewest slots, then the highest aged priority, then the
  oldest, then the holder id.
* AIMD: halve on a rate-limit signal (floor 1), plus one after a full clean window (ceiling), one
  change per window. A decrease applies to new grants; in-flight calls finish.
* The connector takes a slot before the ledger claim and only for a live call (a replay or a
  pre-pulled file needs none), so queueing never consumes a ledger attempt; a slot not granted
  within the bounded wait raises `SlotUnavailable` and the executor re-queues the unit (no retry).
* Every ceiling starts at 1 (`config/parallel.yaml`, proposal) until measured.

## Consequences

* Proven on Postgres: 100 workers over 50 runs and two tenants never exceed the limit under a
  continuous monitor; a dead holder's lease is reclaimed after its TTL; AIMD in SQL; no tenant reads
  or releases another tenant's lease.
* `[VERIFY]` V-GOV-1: ownership transfer to `governor_definer` when the production migrator is not
  a superuser (PostgreSQL 16 `createrole_self_grant`).
* Cloud Tasks queue concurrency is only a coarse backstop (`[VERIFY]` V-TASKS-2).
