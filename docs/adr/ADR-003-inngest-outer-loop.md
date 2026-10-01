# ADR-003 — Inngest as outer trigger, schedule and slice loop

Date: 2026-10-01
Status: Accepted

## Context
Runs need a trigger, scheduled refreshes and a loop that drives 240 s slices. Temporal was
considered; it is heavier to operate for a six-engineer (or smaller) team.

## Decision
Inngest handles triggers, cron and the slice loop (`step.run` per slice, `wait_for_event`
for human approval). LangGraph owns durability (ADR-002); Inngest never retries work that
the graph already retried, and a reconciler re-sends lost events using `event id = run_id`.

## Consequences
Fast to adopt; a managed dependency with platform limits that the slice design must respect
(V4 in the verification register). Local dev uses the Inngest dev server.

## Revisit if
Spike 2 (T11) shows the slice+resume model cannot meet durability targets with Inngest's
step limits, or data-residency requirements exclude the platform (see T7/V4).
