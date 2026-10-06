# Runbook: API latency above 500 ms at p99

Owner: TODO (placeholder, docs/oncall/rotation.md)

## Symptom
The p99 latency ticket fired.

## Impact
Slow console and API; no data loss.

## First checks
Run `queries/slow_queries_hint.sql` (read only; never read tenant content, never bypass RLS, never run an unregistered
script). Open the run timeline and traces by run id when one is named.

## Mitigation
Look for lock waits or saturated pools; scale the API service.

## Escalation
Page or ticket per docs/oncall/severity.md; escalate to the owner above.

## Verification
p99 back under 500 ms for an hour.

## Follow-up
A blameless post-incident review (docs/postmortems/TEMPLATE.md) for every page.
