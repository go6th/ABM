# Runbook: Isolation violation or canary leak (PAGE)

Owner: TODO (placeholder, docs/oncall/rotation.md)

## Symptom
A canary appeared in a sink, the prober saw another tenant's row, or RLS-denied writes spiked.

## Impact
Possible cross-tenant exposure.

## First checks
Run `queries/probe_results.sql` (read only; never read tenant content, never bypass RLS, never run an unregistered
script). Open the run timeline and traces by run id when one is named.

## Mitigation
Preserve evidence (export the alert, the probe rows and the sink sample by id), freeze deploys, page the security owner. Never read tenant content, never bypass RLS, never run an unregistered script.

## Escalation
Page or ticket per docs/oncall/severity.md; escalate to the owner above.

## Verification
Probes and scans clean for 24 hours after the fix.

## Follow-up
A blameless post-incident review (docs/postmortems/TEMPLATE.md) for every page.
