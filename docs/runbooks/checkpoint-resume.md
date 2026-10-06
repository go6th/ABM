# Runbook: Checkpoint resume failures

Owner: TODO (placeholder, docs/oncall/rotation.md)

## Symptom
Resumed slices failed (checkpoint resume SLO).

## Impact
Runs may end FAILED with a refund.

## First checks
Run `queries/failed_runs.sql` (read only; never read tenant content, never bypass RLS, never run an unregistered
script). Open the run timeline and traces by run id when one is named.

## Mitigation
Check checkpoint table health and the graph version of the failing runs.

## Escalation
Page or ticket per docs/oncall/severity.md; escalate to the owner above.

## Verification
Resumes succeed for an hour.

## Follow-up
A blameless post-incident review (docs/postmortems/TEMPLATE.md) for every page.
