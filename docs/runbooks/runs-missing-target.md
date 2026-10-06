# Runbook: Runs missing the duration target

Owner: TODO (placeholder, docs/oncall/rotation.md)

## Symptom
The run-completion SLI (95% within the p95 target, C7) is burning.

## Impact
Accounts take longer than promised.

## First checks
Run `queries/run_durations.sql` (read only; never read tenant content, never bypass RLS, never run an unregistered
script). Open the run timeline and traces by run id when one is named.

## Mitigation
Read the unit timeline (GET /operator/v1/runs/{id}/timeline) for idle time and lease waits; see the provider limit-storm runbook.

## Escalation
Page or ticket per docs/oncall/severity.md; escalate to the owner above.

## Verification
The SLI recovers over its window.

## Follow-up
A blameless post-incident review (docs/postmortems/TEMPLATE.md) for every page.
