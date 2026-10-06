# ADR-0047: cost rollups are derived from the ledger, never a second ledger (D-57)

- Status: Proposed (D-57)
- Date: 2026-10-06
- Deciders: TODO

## Context
Observability work package (docs/prompts/build_observability.md). Observability must not leak tenant
data, change run behaviour, or become a second source of truth for money or run status.

## Decision
`ops_cost_rollups` is recomputed from `tool_calls` by an idempotent upsert keyed by (run, provider
code, route, cost source) in integer micro-dollars. A replay and a pre-pulled file add zero; a call
with unknown cost is counted as unknown, never as zero. `obs.costs.reconcile` compares the rollup
with the ledger exactly; any drift is exported as `cost_drift_total` and pages a ticket. Credits and
dollars stay separate.

## Consequences
A rollup row can always be dropped and rebuilt. Not stored: a per-row replay count (the ledger
does not record replays as rows; the spec's `replays` column is omitted rather than filled with
an invented zero).
