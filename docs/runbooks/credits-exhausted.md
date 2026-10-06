# Runbook: Provider credits exhausted

Owner: TODO (placeholder, docs/oncall/rotation.md)

## Symptom
A provider reported an empty balance; upload requests were raised.

## Impact
Rows end Blocked credits_exhausted.

## First checks
Run `queries/upload_requests.sql` (read only; never read tenant content, never bypass RLS, never run an unregistered
script). Open the run timeline and traces by run id when one is named.

## Mitigation
Top up the provider or supply a pre-pulled file through the upload step (docs/spend/credit-manager-v2.md).

## Escalation
Page or ticket per docs/oncall/severity.md; escalate to the owner above.

## Verification
upload_accepted rows appear and the rows fill on the next run.

## Follow-up
A blameless post-incident review (docs/postmortems/TEMPLATE.md) for every page.
