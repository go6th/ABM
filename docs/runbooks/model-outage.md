# Runbook: Model provider outage or degraded runs

Owner: TODO (placeholder, docs/oncall/rotation.md)

## Symptom
Model calls fail or run degraded (non-primary candidates).

## Impact
Quality may drop; Confirmed claims may be capped.

## First checks
Run `queries/degraded_calls.sql` (read only; never read tenant content, never bypass RLS, never run an unregistered
script). Open the run timeline and traces by run id when one is named.

## Mitigation
Check candidate health; do not edit routing without D-51.

## Escalation
Page or ticket per docs/oncall/severity.md; escalate to the owner above.

## Verification
Degraded share back to normal.

## Follow-up
A blameless post-incident review (docs/postmortems/TEMPLATE.md) for every page.
