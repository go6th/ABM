# Runbook: Provider outage or open circuit

Owner: TODO (placeholder, docs/oncall/rotation.md)

## Symptom
Many failed provider calls; the circuit is open.

## Impact
Rows end Blocked with a reason.

## First checks
Run `queries/provider_failures.sql` (read only; never read tenant content, never bypass RLS, never run an unregistered
script). Open the run timeline and traces by run id when one is named.

## Mitigation
Wait for the circuit's cooldown; check the provider's status page; never add a retry loop.

## Escalation
Page or ticket per docs/oncall/severity.md; escalate to the owner above.

## Verification
Done calls resume.

## Follow-up
A blameless post-incident review (docs/postmortems/TEMPLATE.md) for every page.
