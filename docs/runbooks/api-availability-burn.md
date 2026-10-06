# Runbook: API availability burn

Owner: TODO (placeholder, docs/oncall/rotation.md)

## Symptom
The fast (14.4x over 1 h) or slow (6x over 6 h) burn alert of the 99.9% availability SLO fired.

## Impact
Callers get 5xx or timeouts; runs cannot be created or approved.

## First checks
Run `queries/recent_failed_runs.sql` (read only; never read tenant content, never bypass RLS, never run an unregistered
script). Open the run timeline and traces by run id when one is named.

## Mitigation
Check the latest deploy marker; roll back the revision if it lines up; check `/readyz` (database, config).

## Escalation
Page or ticket per docs/oncall/severity.md; escalate to the owner above.

## Verification
The burn rate falls below the factor in both windows.

## Follow-up
A blameless post-incident review (docs/postmortems/TEMPLATE.md) for every page.
