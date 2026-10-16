# Runbook: Stuck QUEUED or RUNNING runs

Owner: TODO (placeholder, docs/oncall/rotation.md)

## Symptom
The stuck detector recorded runs QUEUED longer than 60 s or RUNNING with no activity.

## Impact
Runs stall; the reconciler should re-send them within 5 minutes.

## First checks
Run `queries/stuck_runs.sql` (read only; never read tenant content, never bypass RLS, never run an unregistered
script). Open the run timeline and traces by run id when one is named.

## Mitigation
The scheduled sweep (`/tasks/sweep`, every 5 minutes) ends a run whose worker died (`worker_lost`),
marks a run with no progress as stalled, and continues both by itself the way Resume does
(`replica.auto_resume`: at most two automatic continuations per research, never for a stop the owner
must fix such as a refused model key, never while a worker holds the lease; log event
`runs.auto_resumed`). Check that the sweep ran (`jsonPayload.event="runs.auto_resumed"` or
`runs.auto_resume_failed`) and the task queue; never edit a run's status by hand.

## Escalation
Page or ticket per docs/oncall/severity.md; escalate to the owner above.

## Verification
No new observation for the run after the reconciler's next pass.

## Follow-up
A blameless post-incident review (docs/postmortems/TEMPLATE.md) for every page.
