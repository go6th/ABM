# Runbook: A broken connector grant (ticket)

Owner: TODO (connectors owner, docs/oncall/rotation.md)

## Symptom
`connector_access_refused` fired: `connector_access_refused_total` above the threshold (proposal:
10 in 15 minutes), labelled by access-log outcome (`denied`, `held`, `error`). A tenant's client-history section is empty or stale.

## Impact
Runs proceed without client history for that tenant (the history is optional input); nothing
outside the tenant's named folders or objects is read. A refusal is the safe direction.

## First checks
1. The outcome label on the metric (dashboard).
2. Run `queries/tenant_overview.sql` to see whether the tenant is suspended.
3. With the tenant admin, in Connectors: the connection's state, granted scopes and named folders.
   A support engineer may look only through a read-only grant the admin gives (view-as).

## Mitigation
`denied` on `token_refresh`: the bounded refresh failed and the connection is marked broken; the
tenant admin reconnects (a new OAuth consent). `denied` at consent: the provider granted scopes
different from `config/connectors.yaml` (`scope_mismatch`); the admin consents again with exactly
those scopes. `denied` on metadata or `held`: an item outside the named folders surfaced or its
ancestry is unknown; it stays quarantined until the admin clears it
(docs/phase3/connector_scope_policy.md). `error`: a provider-side failure.
Never widen a scope, never add a folder for the tenant, never clear a quarantined item.

## Escalation
Ticket per docs/oncall/severity.md; escalate to the connectors owner if several tenants of one
provider fail at once (a provider-side change: check its status page and docs; [VERIFY]).

## Verification
Refusals return to baseline; the next fetch logs `ok` rows in `connector_access_log`.

## Follow-up
If a provider changed scopes or endpoints, update `config/connectors.yaml` through review and
mark the change in docs/open-items.md.
