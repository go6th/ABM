# Runbook: Cache-read ratio below 70%

Owner: TODO (placeholder, docs/oncall/rotation.md)

## Symptom
Cacheable model calls read less than 70% of their input from the cache.

## Impact
Higher model cost.

## First checks
Run `queries/cache_ratio.sql` (read only; never read tenant content, never bypass RLS, never run an unregistered
script). Open the run timeline and traces by run id when one is named.

## Mitigation
Look for a prompt or prefix change (a timestamp or id in the prefix); revert it.

## Escalation
Page or ticket per docs/oncall/severity.md; escalate to the owner above.

## Verification
Ratio above 70% for an hour.

## Follow-up
A blameless post-incident review (docs/postmortems/TEMPLATE.md) for every page.
