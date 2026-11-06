# ADR-0119: A Kimi or Grok fallback to Claude is a billing event

- Status: Accepted
- Date: 2026-11-03
- Deciders: product owner (fix prompt C, task 4, 2026-11-03)

## Context

A research step whose chosen model (Kimi K3 or Grok 4.5) failed its schema twice falls back to the
role's Claude default (`agents.production.wiring.RunDepsModel`, owner decision 2026-10-14) and is
recorded in the run's `models.fallbacks`, but not what it cost, so the user saw a bill above the
estimate with no reason. `usage_events.kind` is limited by `ck_usage_events_kind` (migration 0025)
and the table had no column for event details.

## Decision

The behaviour stays. `RunDepsModel` prices every attempt (`llm.reply_cache.price`); the run's
fallback record carries both model ids, `discarded_usd` (the paid, discarded attempts on the chosen
model) and `extra_usd` (the Claude calls that replaced them). In the same transaction it writes one
`usage_events` row of kind `model_fallback` (subject `run:step:request-digest`, idempotent, units
1) whose new `detail` object holds the same facts; migration 0100 adds the kind and the column
(the down migration keeps recorded events: the restored constraint is NOT VALID). `GET
/v1/runs/{id}` returns `token_use.fallbacks` (new `ModelFallback` in the API contract) and the
run's token card (`web/components/ws/TokenMeter.tsx`) says how many steps moved to which model and
what that added to the bill. Rejected: emitting the event without its costs (the user's question is
the amount).

## Consequences

Usage summaries count `model_fallback` events per period. No replay key changes.
