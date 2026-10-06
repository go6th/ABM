# Runbook: Telemetry down (silence is an alert)

Owner: TODO (placeholder, docs/oncall/rotation.md)

## Symptom
No heartbeat, or telemetry drops grow.

## Impact
Blind spot; alerts may not fire.

## First checks
Run `queries/recent_activity.sql` (read only; never read tenant content, never bypass RLS, never run an unregistered
script). Open the run timeline and traces by run id when one is named.

## Mitigation
Check the collector and exporter health; the product keeps running (telemetry never blocks it).

## Escalation
Page or ticket per docs/oncall/severity.md; escalate to the owner above.

## Verification
Heartbeats arrive and drops stop growing.

## Follow-up
A blameless post-incident review (docs/postmortems/TEMPLATE.md) for every page.
