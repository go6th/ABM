# Runbook: The refresh scheduler is stalled (PAGE)

Owner: TODO (refresh owner, docs/oncall/rotation.md)

## Symptom
`refresh_scheduler_stalled`: no `refresh_scheduler_heartbeat` for 30 minutes (proposal). Probes and
refreshes are not being claimed.

## Impact
Signals are not checked; alerts stop arriving (the 95%-within-24-hours SLO burns after a day).
Nothing is lost: slots are created per (account, kind, period) and caught up later.

## First checks
1. The worker service health and its logs (identifiers only) for the scheduler tick.
2. Cloud Scheduler and Cloud Tasks status for the tick job ([VERIFY] V-P4-SCHED-1).
3. Database connection pool and lease metrics on the observability dashboard.

## Mitigation
Restart the worker. On recovery the tick catches up missed slots in bounded, jittered batches
(`config/refresh.yaml` `catch_up`: oldest due first, per-tenant cap), so there is no stampede.
Slots left `claimed` by a dead worker are recovered by the lease (`refresh.slots.recover`). Never
insert slots or runs by hand.

## Escalation
Page per docs/oncall/severity.md; escalate to the refresh owner after 1 hour.

## Verification
The heartbeat resumes; `refresh_probes_total` rises; the catch-up backlog drains within a day.

## Follow-up
A post-incident review; if catch-up took more than a day, revisit the catch-up bounds.
