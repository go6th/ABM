# Operator route and API map

The operator console uses a separate staff session. Browser requests go to same-origin Next.js API routes, which keep the Google ID token in an HttpOnly cookie and call the backend. API response bodies are checked against generated Zod schemas before the screen renders them.

| Screen | Backend contract | Status and access |
|---|---|---|
| /operator/login | Google Workspace authorization code; /api/operator/login, /api/operator/callback, /operator/v1/session | Implemented; requires deployment OAuth and staff allowlist values |
| /operator | /operator/v1/session, /operator/v1/tenants, /operator/v1/flags, /operator/v1/keys, /operator/v1/pilot/aggregate | Connected; tenant inventory is content-free; flags, keys and pilot data require operator role |
| /operator/tenants | GET/POST /operator/v1/tenants; suspend, reactivate, plan and quota routes | Connected; changes are role checked and audited by the backend |
| /operator/configuration | GET/POST /operator/v1/flags, GET/POST /operator/v1/keys | Connected; key reference is write-only; operator role required |
| /operator/support | /operator/v1/view/accounts, /operator/v1/view/settings | Connected through a tenant-approved support grant; persistent read-only banner; tenant-visible audit |
| /operator/runs | /operator/v1/runs/{run_id}/timeline | Connected by run ID and support grant; no global run directory exists |
| /operator/spend | /operator/v1/runs/{run_id}/spend/live | Connected by run ID and support grant |
| /operator/cost | /operator/v1/ops/cost/{run_id} | Connected by run ID and support grant |
| /operator/failures | /operator/v1/ops/failures, /operator/v1/ops/stuck | Connected by support grant; tenant-scoped aggregates |
| /operator/review | No operator review-queue route | Development-only explanation; hidden in production |
| /operator/monitors | No operator health/SLO route | Development-only explanation; hidden in production |
| /operator/audit | Operator audit write exists, no read endpoint | Development-only explanation; hidden in production |
| /operator/announcements | No operator status or announcement route | Development-only explanation; hidden in production |

Tenant data reads require a UUID support grant. The UI does not persist it. The backend resolves owner, scope, expiry, revocation and fresh MFA on every request. Platform administration uses the staff role directly and does not expose tenant content.

Not yet exposed as UI actions: the two-person tenant deletion workflow and pre-pulled upload workflow. The API has those operations, but their review and confirmation flows need dedicated screens. Global run lists, unit-cost aggregates, SLOs, audit browsing, review queue and announcements need backend contracts before production screens can be enabled.

The customer and operator sign-in screens are captured at [client-login.png](client-login.png) and
[operator-login.png](operator-login.png). Both production standalone routes loaded without failed
requests or browser console errors. The operator screenshot shows the unconfigured state; Google
OAuth client, callback, Workspace domain and verified staff email are deployment inputs.
