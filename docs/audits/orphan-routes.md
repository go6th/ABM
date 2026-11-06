# Orphan routes: /v1 routes no web screen calls (2026-11-03)

Fix prompt B, task 4. Method: every `/v1` operation in the dev app's OpenAPI (`SERVICE_ROLE=all`,
170 operations) was matched against the literal paths in `web/app`, `web/components`, `web/lib`
and `web/ui` (the generated client `web/lib/gen` excluded), then each candidate was checked by hand
for paths the web builds from variables (`${api}/tabs/…`, `/sheets/${key}/${format}`). The audit
PDF counted 73; the heuristic found 82, of which 13 turned out to be called (false positives,
listed at the end) and 5 are called by a browser redirect, not by web code. **64 routes have no
caller.** Nothing was deleted in this pass; deletions are listed for fix prompt F.

Recommendation key: **(a)** keep, 404 behind a client feature flag until a screen exists
(`@behind_flag`, `src/account_intelligence/api/flags.py`); **(b)** delete the route and its
handler (prompt F); **(c)** keep as is and wire a screen (product work).

## 1. Connectors and CRM: (a), implemented

Product work in progress (data connectors and CRM write-back). Flags `connectors` and `crm`.

| Route | Flag |
|---|---|
| GET /v1/connectors, POST /v1/connectors | connectors |
| GET /v1/connectors/oauth/callback | connectors |
| POST /v1/connectors/{connection_id}/grants, /fetch, /disconnect | connectors |
| GET /v1/connectors/{connection_id}/quarantine | connectors |
| POST /v1/connectors/{connection_id}/quarantine/{item_id}/{decision} | connectors |
| POST /v1/crm/{crm}/enable, /revoke, /mappings, /preview | crm |
| GET, POST /v1/crm/{crm}/writes; POST /v1/crm/{crm}/writes/approve | crm |

15 routes. `crm_writeback` stays (prompt F task 1): a flag reaches it.

## 2. Subscriptions, alerts and signals (the monitoring product): (a), implemented

Flag `monitoring`. The engine behind them (`src/refresh`, `src/signals`, `src/alerts`) is not wired
(prompt F task 2 decides whether it is archived; if it is, these nine routes move to (b)).

| Route |
|---|
| GET /v1/signals |
| GET /v1/alerts; POST /v1/alerts/{alert_id}/feedback; GET /v1/alerts/usefulness |
| GET, PUT /v1/subscriptions; POST /v1/subscriptions/subscribe-all |
| GET /v1/monitoring/budget |
| GET /v1/accounts/{domain}/history |

9 routes.

## 3. Usage, audit and spend: (c)

The tenant audit log and usage export are compliance and billing features a client admin needs;
the screens were removed (`/usage`, `/audit`), the routes are correct and tested.

GET /v1/usage, GET /v1/usage/export, GET /v1/audit, GET /v1/audit/export, GET /v1/credits,
GET /v1/runs/{run_id}/spend (the run page calls `/spend/live` only). 6 routes.

## 4. Saved views: (b), owner confirmation needed

GET /v1/views, POST /v1/views, PUT /v1/views/{view_id}, POST /v1/views/{view_id}/archive.
4 routes. Default (b) per the fix prompt, but ADR-0051 (tenant roles) names "editing shared saved
views" as an analyst capability and `tests/phase3/test_ws4_views.py` tests them: that is an ADR
saying otherwise, so prompt F deletes them only after the owner confirms the capability is dropped.

## 5. Parity bundle: (b) for the edit endpoint, keep the reads

POST /v1/accounts/{domain}/parity/edits: (b), no caller, no ADR. The reads
(GET …/parity/bundle, …/parity/manifest, …/parity/versions) are probed by the isolation matrix
and feed the parity workspace (`ParityWorkspace.tsx` reads `…/parity/files/…`); they stay (c).
4 routes.

## 6. Everything else

| Route | Recommendation | Why |
|---|---|---|
| GET, POST, PUT /v1/sso/connections | (c) | a client admin's IdP setup; operators do it today |
| GET /v1/sso/sessions, DELETE /v1/sso/sessions/{session_id}, POST /v1/sso/logout-all | (c) | session revocation is a security control; the security screen was removed |
| POST /v1/auth/password/enroll | (c) | local password enrolment, no screen |
| GET, PUT, DELETE /v1/chat/provider-key | (b) | the "model key" screen was removed; Ask uses platform keys |
| GET /v1/branding, PUT /v1/settings/branding, POST /v1/settings/branding/logo, GET, PUT /v1/settings | (c) | client branding; operators set it today |
| POST /v1/documents/estimate, GET, PUT /v1/documents/policy, POST /v1/documents/{document_id}/authoritative | (c) | document admin; no screen |
| GET /v1/exports/{package_id} | (c) | the run page lists files from the run, not the package |
| POST /v1/chat/threads/{thread_id}/turns/stream | (b) | superseded by the Ask stream route the web calls |
| GET /v1/runs/pull-option | (b) | no caller since the New research form changed |
| POST /v1/runs/{run_id}/cap/extend, /cap/reject, /cap/pauses/{request_id}/approve, /decline | (c) | the run page approves (`/cap/approve`); extend, reject and per-pause answers have no screen. With the caps back on (ADR-0106) a paused run can be approved but not declined from the web |
| GET, POST /v1/feedback, POST /v1/usefulness, GET /v1/usefulness/metric | (c) | pilot feedback; the operator pilot aggregate reads it |
| GET /v1/accounts/{domain}/deliverables, GET …/file-analysis history | (c) | the account page lists files another way |

## Called by a redirect, not by web code (not orphans)

GET /v1/sso/{slug}/start (the sign-in page follows the `start_url` the API returns),
GET /v1/sso/oidc/callback and POST /v1/sso/saml/acs (the IdP), GET /v1/mail-and-drive/callback
(Google or Microsoft), PUT /v1/documents/{document_id}/content (the local stand-in for a signed
upload URL the API returns).

## False positives of the heuristic (called)

GET …/sheets/{sheet}/tabs/{tab}, …/xlsx, …/csv; GET …/file-analysis; GET /v1/settings/people-consent
(not /v1/settings); GET /v1/runs/{run_id}/collected (archive view). Re-run the inventory with the
script in the fix-prompt notes before deleting anything in prompt F.

## Deletion list for prompt F

(b) above: POST /v1/accounts/{domain}/parity/edits; GET, PUT, DELETE /v1/chat/provider-key;
POST /v1/chat/threads/{thread_id}/turns/stream; GET /v1/runs/pull-option; the four saved-view
routes only after the owner confirms (ADR-0051).
