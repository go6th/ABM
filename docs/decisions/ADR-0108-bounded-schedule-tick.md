# ADR-0108: one scheduled-refresh tick starts at most 10 runs, 3 per client

- Status: Accepted
- Date: 2026-11-03
- Deciders: owner (fix prompt A, task 4)

## Context

`POST /tasks/schedules-tick` (Cloud Scheduler, every 15 minutes) called
`replica/schedules.tick`, which started every due scheduled refresh (up to the lookup's 500) and
then every waiting job the credit pool could cover. With the research caps off nothing else
bounded it: a batch of due accounts, or the 1st of the month promoting every waiting job, started
that many paid runs at once.

## Decision

Two platform defaults (migration 0098): `schedule_tick_max_starts` (default 10) and
`schedule_tick_max_per_tenant` (default 3, at most the overall ceiling). One `StartBudget` covers
both phases of a tick (scheduled refreshes, then promoted waiting jobs; `promote_waiting` takes a
`limit`). Due accounts are taken round robin across clients, each client's oldest first
(`fair_order`), so one large client cannot use a whole tick. An account over the ceiling is not
touched: its `next_refresh_at` stays and the next tick picks it up; a waiting job over the ceiling
stays waiting. The tick reports `deferred`. Tests: `tests/unit/test_schedule_tick_bounds.py`
(order and budget) and `tests/replica/test_schedule_tick_bounds.py` (25 due accounts across 4
tenants on PostgreSQL: at most 10 per tick, 3 per tenant, all 25 started over three ticks).

## Consequences

A burst of N due accounts now takes ceil(N / 10) ticks (15 minutes each) to start. Both values
are runtime settings on the operator Defaults page ("Scheduled refreshes started at once", "Per
client per tick"; `PUT /operator/v1/console/defaults`, audited): the tick reads them on every run,
so a change needs no deploy. A per-client value above the tick's own is refused with 422
(`tests/replica/test_admin_console.py`, `web/tests/unit/defaults-tick-limits.test.tsx`).
