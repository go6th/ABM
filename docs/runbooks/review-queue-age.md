# Runbook: Review queue age

Owner: TODO (placeholder, docs/oncall/rotation.md)

## Symptom
Runs wait in review too long (SourceMissing until the review work package exists).

## Impact
Deliveries delayed.

## First checks
Run `queries/awaiting_runs.sql` (read only; never read tenant content, never bypass RLS, never run an unregistered
script). Open the run timeline and traces by run id when one is named.

## Mitigation
Notify reviewers; never auto-approve.

## Escalation
Page or ticket per docs/oncall/severity.md; escalate to the owner above.

## Verification
Queue age back under the configured age.

## Follow-up
A blameless post-incident review (docs/postmortems/TEMPLATE.md) for every page.
