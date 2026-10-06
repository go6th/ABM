# Runbook: Quality gate failure spike

Owner: TODO (placeholder, docs/oncall/rotation.md)

## Symptom
More runs than usual end PARTIAL with a gate reason.

## Impact
Deliveries are held.

## First checks
Run `queries/partial_reasons.sql` (read only; never read tenant content, never bypass RLS, never run an unregistered
script). Open the run timeline and traces by run id when one is named.

## Mitigation
Read the gate defects of a sample run; fix the builder or the content rule.

## Escalation
Page or ticket per docs/oncall/severity.md; escalate to the owner above.

## Verification
PARTIAL share back to normal.

## Follow-up
A blameless post-incident review (docs/postmortems/TEMPLATE.md) for every page.
