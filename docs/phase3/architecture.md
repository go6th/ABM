# Phase 3 architecture: the tenant plane

Spec: `docs/prompts/build_phase3.md`. Decisions D-61 to D-70 (ADR-0051 to ADR-0060), conflicts C23
to C26. Migrations 0020 to 0027 (expand only).

## Planes

| Plane | Who | Entry | Tenant comes from |
|---|---|---|---|
| Tenant (`/v1`) | members (admin, analyst, seller, viewer) | session cookie or bearer token | the verified session or token, never a request field |
| Sign-in (`/v1/sso/*`, public) | anonymous browsers | IdP redirects and posts | the tenant's own IdP connection, then the login state bound to the browser |
| Operator (`/operator/v1`) | 6th staff (`operator`, `support`) | staff IdP bearer token | a support grant (`X-Support-Grant`) or, for lifecycle and quota changes only, a tenant id an operator names in the path |
| Worker (`/tasks`) | the job runner | worker token | the task payload, checked against the run |

## Request path (tenant plane)

1. `route_guard` (router dependency): the request's route template must be in the route catalog
   (`api.policy`), else 403. Deny by default.
2. `authorize`: verified identity, role allowed for the route's action, membership re-checked
   through a 60 s cache (removal and role change take effect within it), CSRF on cookie writes,
   step-up where the action needs it, the read audit row (fail closed).
3. The handler opens `tenant_txn(identity.tenant_id)`: a transaction-local `app.tenant_id` under the
   `app_rw` role. Every tenant table has forced RLS with policies `TO app_rw`; no handler passes a
   tenant id it read from the request.
4. Problems are RFC 7807 documents with the request id; `NotConfigured` ports answer 503.

## Packages

| Package | Role |
|---|---|
| `tenancy` | settings versions and exclusions, lifecycle (suspend, deletion cascade), storage paths and signed downloads, cache keys, the object catalog behind the isolation matrix |
| `identity` | roles table, OIDC, SAML, broker, sessions, CSRF, membership, provisioning, SSRF-guarded IdP client |
| `audit` | events, per-tenant hash chain, tenant export by category, route resource ids |
| `views` | row sources from the export mapping, materialised `view_rows`, allow-listed query builder, saved views, parity, packages, branding, safe rendering, progress |
| `connectors_client` | OAuth with PKCE, exact scopes, Drive ancestry, quarantine, access log, bounded fetch; Salesforce, HubSpot adapters; Gmail and notes `NotConfigured` |
| `metering` | usage events, atomic limits, summary, reconcile |
| `admin` | support grants, quotas and plans, feature flags, broker key references, tenant management |
| `pilot` | feedback, usefulness events, metric |
| `web/` | Next.js 16 client app (strict TypeScript, nonce CSP in `proxy.ts`) |

## Isolation is proven, not reviewed

`scripts/gen_isolation_matrix.py --check` discovers every tenant table, view, definer function,
route, storage layout, cache purpose, event type and job and fails on any without an entry in
`tests/isolation/matrix/matrix.yaml`. `tests/isolation/test_matrix.py` plants canaries for three
tenants in every table and probes cross-tenant reads and writes, and calls every route as tenant B
(or as a staff operator) with the authorisation layer replaced, asserting no value of tenant A's
canaries appears. A deliberately permissive policy in a scratch table must be caught.

## Loops

Every loop is bounded and listed in `docs/architecture/loops.md` (generated): token refresh,
connector fetch pages, audit chain verification batches, usage reconcile, SSE heartbeat and max
duration, deletion passes.
