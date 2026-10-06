# Runbook: Workflow engine (task queue) outage

Owner: TODO (placeholder, docs/oncall/rotation.md)

## Symptom
Slices are not dispatched; QUEUED runs pile up.

## Impact
No progress on any run.

## First checks
Run `queries/queued_runs.sql` (read only; never read tenant content, never bypass RLS, never run an unregistered
script). Open the run timeline and traces by run id when one is named.

## Mitigation
Restore the queue; the reconciler re-sends runs QUEUED for more than 60 s (ARCH).

## Escalation
Page or ticket per docs/oncall/severity.md; escalate to the owner above.

## Verification
No run stuck beyond 5 minutes after recovery.

## Follow-up
A blameless post-incident review (docs/postmortems/TEMPLATE.md) for every page.
