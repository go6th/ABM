# Runbook: Cost drift or unknown cost

Owner: TODO (placeholder, docs/oncall/rotation.md)

## Symptom
The rollup differs from the ledger, or calls have unknown cost.

## Impact
Spend reports may be wrong.

## First checks
Run `queries/cost_rollups.sql` (read only; never read tenant content, never bypass RLS, never run an unregistered
script). Open the run timeline and traces by run id when one is named.

## Mitigation
Re-run the rollup refresh for the run (idempotent); add the missing price with its source.

## Escalation
Page or ticket per docs/oncall/severity.md; escalate to the owner above.

## Verification
Reconcile reports zero drift.

## Follow-up
A blameless post-incident review (docs/postmortems/TEMPLATE.md) for every page.
