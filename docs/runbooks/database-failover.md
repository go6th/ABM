# Runbook: Database failover

Owner: TODO (placeholder, docs/oncall/rotation.md)

## Symptom
Connection errors, then recovery on a new primary.

## Impact
Requests and slices fail during the switch.

## First checks
Run `queries/connections.sql` (read only; never read tenant content, never bypass RLS, never run an unregistered
script). Open the run timeline and traces by run id when one is named.

## Mitigation
Confirm the new primary; let the reconciler re-send QUEUED runs; check resume success.

## Escalation
Page or ticket per docs/oncall/severity.md; escalate to the owner above.

## Verification
readyz green; no stuck runs.

## Follow-up
A blameless post-incident review (docs/postmortems/TEMPLATE.md) for every page.
