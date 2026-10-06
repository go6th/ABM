# Runbook: A canary string in a log (PAGE)

Owner: TODO (placeholder, docs/oncall/rotation.md)

## Symptom
The sink canary scan found a planted canary in logs, spans or another sink.

## Impact
Content may be leaking into telemetry.

## First checks
Run `queries/probe_results.sql` (read only; never read tenant content, never bypass RLS, never run an unregistered
script). Open the run timeline and traces by run id when one is named.

## Mitigation
Follow isolation-violation.md: preserve evidence, freeze, find the call site that bypassed the allow-list.

## Escalation
Page or ticket per docs/oncall/severity.md; escalate to the owner above.

## Verification
The next scan is clean.

## Follow-up
A blameless post-incident review (docs/postmortems/TEMPLATE.md) for every page.
