# ADR-0004: Durable execution and paid-call dispatch

- Status: Proposed; local spike passed, stakeholder sign-off pending
- Date: 2026-10-02
- Deciders: Tech lead, analyst owner, product owner
- Governing conflict: Skill C1 in `references/00-sources-and-conflicts.md`

## Context

The governing architecture selects LangGraph Postgres checkpoints as the workflow durability source of truth, with Inngest for event triggers, schedules and slices. The roadmap's Temporal recommendation is subordinate to C1: Temporal is the fallback only if durability spike 2 fails. The starter ledger previously left a crash window between a provider call and ledger persistence and allowed concurrent duplicate dispatch.

## Decision proposal

Keep LangGraph plus Inngest as the reversible default. A `tool_call_intents` row is committed before any paid call. A unique tenant/run/argument key prevents concurrent workers from claiming the same call. A response and completed intent are committed together. An error marks the intent `UNKNOWN`; a process crash leaves it `IN_FLIGHT`. Either state blocks automatic redispatch. A per-run database lease prevents overlapping graph workers and expires after the configured slice plus a safety interval.

This is **at-most-once dispatch**, not exactly-once provider execution. Without a provider-supported idempotency key or a provider reconciliation API, it is impossible to prove whether a timed-out external call was billed. Temporal would improve orchestration durability but would not by itself make an external side effect exactly once. If the measured spike shows LangGraph/Inngest cannot resume reliably, evaluate Temporal under C1; if the product requires exactly-once billing, require provider idempotency/reconciliation regardless of workflow engine.

## Evidence and remaining verification

- Implementation: `src/agents/graph.py`, `src/agents/runner.py`, `migrations/0003_data_layer_v1.sql`.
- `PGPORT=5433 PGPASSWORD=dev PGADMINUSER=postgres TEST_DB=ai APP_RW_PASSWORD=local-dev-only MIGRATOR_DATABASE_URL='postgresql://postgres:dev@localhost:5433/ai' CHECKPOINT_DATABASE_URL='postgresql://app_rw:local-dev-only@localhost:5433/ai' uv run --frozen pytest -m 'not eval' --cov=src --cov-fail-under=80` passed locally: 79 passed, 1 expected failure, 81.85% coverage. The suite includes five separate worker-process kills after a paid stub response is durable but before its graph node checkpoint; every resumed graph completed, and a forced-RLS PostgreSQL invocation counter recorded each branch's stub exactly once. It also tests ambiguous-call blocking and the two-worker lease.
- The direct RLS catalog query against the migrated isolated PostgreSQL database returned no violations. `make check` itself could not finish because the host `psql` client is unavailable; the migration, catalog, and pytest database gates were run directly using PostgreSQL and psycopg.
- No paid provider was called. These are local stub/checkpoint results, not a real broker/model benchmark.
- Required before acceptance: tech lead, analyst owner and product owner review/sign-off; confirm checkpoint restore/version and provider idempotency/reconciliation terms. No sign-off is inferred from this local test.

## Consequences

Ambiguous calls stop for operator reconciliation instead of being retried. This favors avoiding duplicate charges over automatic progress. The 15-minute intent lease expiry is diagnostic only; it does not authorize a second dispatch. A manual reconciliation/resolution workflow and provider-specific idempotency support remain required before paid production workloads.
