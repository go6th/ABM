# API v1 implementation status

Updated 2026-10-04 on `feat/api-v1`.

This is an implementation slice, not completion of the API v1 prompt. The repository already
contains a run creation owner (`runs.lifecycle.create_run`) and the existing `/v1/runs` create,
status, cancel and spend routes. This slice adds authentication membership rechecks, a sanitized
problem response envelope with request IDs, status ETags, and reusable signed cursors.

## Implemented in this slice

- JWT claims require `sub`, `tenant_id`, `role`, `exp` and `iat`; signature, issuer and audience
  checks remain pinned to configured RS256 settings.
- Authenticated requests re-check the membership role inside `tenant_txn()`. A database error
  fails closed with 503. This currently checks every request rather than using the requested
  approximate 60-second cache; a shared cache adapter is not present.
- HTTP failures have a problem-details response envelope with a request ID. Framework detail text
  is reduced to a short allow-list so internal and untrusted text is not reflected.
- Run status responses have a representation ETag and honor exact `If-None-Match` with 304.
- Cursor primitives authenticate their payload and bind it to a tenant and collection. No list
  endpoint uses them yet.

## Not implemented or proven

Accounts create/list, run listing and events, artifact delivery, API key lifecycle, dedicated
rate limiting, a route policy matrix, OpenAPI snapshot, operator plane, and console are incomplete.
The operator metadata-only view and `operator_ro` isolation exception have not been established;
do not expose cross-tenant operator routes before verifying forced RLS and catalog behavior.
Domain resolution and blocked-entity checks are not wired into the API, so the existing run route
does not yet meet input guard A5. The run route still accepts legacy `lanes_off` and
`pursuing_client` fields. Account creation, resolver behavior, artifact gate state, and signer
behavior need their owning services before those routes can safely be added.

The integration/isolation gates require PostgreSQL. In this environment the test fixture cannot
authenticate to localhost PostgreSQL (`fe_sendauth: no password supplied`), so no database or
RLS claim is made. No API key tables or other migrations were added.

## Product decisions from the prompt

D-32 through D-37 remain proposals requiring product-owner review. Values with no source (rate
limits, cursor page size, API key expiry/overlap, signed-link lifetime and console presentation)
remain TODO and are not selected here. `[VERIFY]` items include production secret retrieval,
Cloud Run link signing behavior, and the console toolchain/browser availability.
