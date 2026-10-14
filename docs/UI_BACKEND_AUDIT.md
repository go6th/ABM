# UI and backend audit (UI redesign, 2026-10-11)

> Phase 4C (2026-10-15): the parity suite (`web/e2e/parity/`, `playwright.parity.config.ts`) was removed
> as dead code. The current screen check is `web/e2e/replica/screens.spec.ts` (axe, CSP, overflow at
> 1360 and 375). This page is kept as the record of the 2026-10-11 redesign.

This audit is the input to the light and dark rebuild of every screen. It starts from the
`pre-ui-redesign` tag (e01c565) plus the in-progress web work adopted on `feat/ui-redesign`
(bbcb181, b865997).

- **Schema:** `docs/api/openapi.json`, 142 operations.
- **Running stack:** the hermetic E2E server (`tests/e2e/serve.py`). It runs the real API over a
  test database, with two seeded clients (`slug_a`, `slug_b`), a fake OIDC IdP and a synthetic
  model and providers.
- **Operator access:** operators use the dev staff verifier (`DevStaffVerifier`, `APP_ENV=test`),
  which accepts `operator:<name>` and `support:<name>` tokens. No real Google client id is used.
- **"Works":** the parity suite (`web/e2e/parity/`) performs the action against that stack and
  gets the expected result.

## Decision key

| Decision | Meaning |
|---|---|
| KEEP | Operation exists and works; redesign |
| FIX | Exists, broken by a front end bug; fix, then keep |
| BLOCKED | Backend defect; hidden behind a flag, listed for the owner |
| DELETE | No backing operation; remove the screen or element |
| BUILD | Backend capability with no UI; build it |
| NONE | Internal; no UI |

## 1. Backend capabilities

### Client plane (`/v1`)

| Capability | Operations | UI | Decision |
|---|---|---|---|
| Password sign-in | `POST /v1/auth/login` (including workspace selection on 409) | Sign in | KEEP |
| Email verification | `POST /v1/auth/email/verify`, `POST /v1/auth/email/resend` | Verify email, sign in | KEEP |
| Password reset | `POST /v1/auth/password/reset/request`, `POST /v1/auth/password/reset` | Forgot, reset | KEEP |
| Password enrolment | `POST /v1/auth/password/enroll` | Security | KEEP |
| Enterprise SSO start | `GET /v1/sso/{slug}/start` | Sign in, SSO tab | KEEP |
| SSO callbacks | `GET /v1/sso/oidc/callback`, `POST /v1/sso/saml/acs` | none | NONE |
| Session | `GET /v1/sso/session`, `POST /v1/sso/logout`, `POST /v1/sso/logout-all` | shell, Security, Users | KEEP |
| Sessions list | `GET /v1/sso/sessions`, `DELETE /v1/sso/sessions/{id}` | Security | KEEP |
| SSO connections | `GET /v1/sso/connections` | SSO settings | KEEP |
| SSO connection admin | `POST /v1/sso/connections`, `PUT /v1/sso/connections/{id}` | none | BUILD |
| Profile | `GET /v1/me` | shell | KEEP |
| Branding | `GET /v1/branding`, `PUT /v1/settings/branding`, `POST /v1/settings/branding/logo` | Workspace settings | KEEP |
| Workspace settings | `GET /v1/settings`, `PUT /v1/settings` | Workspace settings, Refresh | KEEP |
| Accounts | `GET /v1/accounts`, `POST /v1/accounts`, `GET /v1/accounts/{domain}` | Accounts, Account | KEEP |
| Account tables | `GET /v1/accounts/{domain}/tables/{table}` | Deep dive | KEEP |
| Account history | `GET /v1/accounts/{domain}/history` | Account, Signals | KEEP |
| Saved views | `GET /v1/views`, `POST /v1/views` | Deep dive | KEEP |
| Saved view edit | `PUT /v1/views/{id}`, `POST /v1/views/{id}/archive` | none | BUILD |
| Runs | `GET /v1/runs`, `POST /v1/runs`, `GET /v1/runs/{id}`, `POST /v1/runs/{id}/approve`, `POST /v1/runs/{id}/cancel` | Runs, Run, Refresh | KEEP |
| Run progress | `GET /v1/runs/{id}/progress`, `GET /v1/runs/{id}/events` | Account, Run | KEEP |
| Pull option (phase runs) | `GET /v1/runs/pull-option` | old account page only | BUILD (in Refresh) |
| Spend cap | `GET /v1/runs/{id}/cap`, `POST .../cap/approve`, `.../cap/reject`, `.../cap/extend`, `.../cap/pauses/{rid}/approve`, `.../decline` | none | BUILD (Run) |
| Live spend | `GET /v1/runs/{id}/spend/live` (categories, no provider) | none | BUILD (Run) |
| Spend summary | `GET /v1/runs/{id}/spend` (untyped record) | none | NONE: untyped, overlaps live spend |
| Exports | `POST /v1/exports`, `GET /v1/exports/{id}/files/{n}` | Account, Deep dive | KEEP |
| Export history | `GET /v1/exports`, `GET /v1/exports/{id}` | none | BUILD (Downloads) |
| Chat | `GET/POST /v1/chat/threads`, `GET/DELETE /v1/chat/threads/{id}`, `POST .../turns/stream` | Home, Conversations, Account | KEEP |
| Provider key | `GET/PUT/DELETE /v1/chat/provider-key` | Settings, provider key | KEEP |
| Models | `GET /v1/models` (provider names and USD prices) | none | NONE: model choice is automatic, and client copy may not name providers |
| Signals | `GET /v1/signals` | Home | KEEP |
| Alerts | `GET /v1/alerts`, `POST /v1/alerts/{id}/feedback` | Alerts | KEEP |
| Alert usefulness | `GET /v1/alerts/usefulness` | none | BUILD (Alerts, admin) |
| Subscriptions | `GET/PUT /v1/subscriptions`, `POST /v1/subscriptions/subscribe-all` | Alert preferences | KEEP |
| Monitoring allowance | `GET /v1/monitoring/budget` | Alert preferences | KEEP |
| Feedback | `POST /v1/feedback`, `POST /v1/usefulness` | Deep dive | KEEP |
| Feedback review | `GET /v1/feedback` | none | BUILD (Feedback, admin) |
| Usefulness metric | `GET /v1/usefulness/metric` | none | BUILD (Feedback, admin) |
| Users | `GET/POST/PATCH /v1/users` | Team | KEEP |
| Usage | `GET /v1/usage` | Usage | KEEP |
| Usage export | `GET /v1/usage/export` | none | BUILD (Usage) |
| Audit | `GET /v1/audit`, `GET /v1/audit/export` | Audit | KEEP |
| Connectors | `GET/POST /v1/connectors`, `POST .../disconnect`, `GET .../quarantine`, `POST .../quarantine/{item}/{decision}` | Integrations | KEEP |
| Connector grants and fetch | `POST /v1/connectors/{id}/grants`, `POST /v1/connectors/{id}/fetch` | none | BUILD (Integrations) |
| Connector callback | `GET /v1/connectors/oauth/callback` | none | NONE |
| CRM write-back | `POST /v1/crm/{crm}/enable`, `/revoke`, `/mappings`, `/preview`, `GET/POST /writes`, `POST /writes/approve` | CRM | KEEP |
| Support access | `GET/POST /v1/support/grants`, `POST .../revoke`, `GET /v1/support/events` | Support access | KEEP |
| Health | `GET /healthz`, `GET /readyz` | none | NONE |

### Operator plane (`/operator/v1`, through `/api/operator/v1`)

| Capability | Operations | UI | Decision |
|---|---|---|---|
| Staff session | `GET /operator/v1/session` | sign in, shell | KEEP |
| Clients (tenants) | `GET/POST /operator/v1/tenants`, `POST .../suspend`, `/reactivate`, `/plan`, `/quotas` | Overview, Clients | KEEP |
| Client deletion | `POST .../deletion`, `.../{rid}/dry-run`, `.../{rid}/approve`, `.../{rid}/execute` | none | BUILD (Client detail) |
| Flags and keys | `GET/POST /operator/v1/flags`, `GET/POST /operator/v1/keys` | Overview, Configuration | KEEP |
| Pilot aggregate | `GET /operator/v1/pilot/aggregate` (counts only, no question text) | Overview | KEEP |
| Support view | `GET /operator/v1/view/accounts`, `GET /operator/v1/view/settings` (needs a client grant) | Support view | KEEP |
| Run inspection | `GET /operator/v1/runs/{id}/timeline`, `GET /operator/v1/ops/cost/{id}`, `GET /operator/v1/runs/{id}/spend/live` | Runs, Cost, Spend | KEEP |
| Failures | `GET /operator/v1/ops/failures`, `GET /operator/v1/ops/stuck` | Failures | KEEP |
| Pre-pulled upload | `POST .../prepulled/intents`, `GET .../prepulled/{intent}`, `POST .../prepulled/{intent}/complete` | none | BUILD (Run upload) |

## 2. Screens and decisions

Every element below was read from the source. The parity suite proves the KEEP rows against
the running stack (see the Parity column in `docs/SCREEN_MATRIX.md`).

### Client

| Route | Elements and operations | Decision |
|---|---|---|
| `/login` | Email and password (`login`); workspace picker on 409; show password (pure UI); forgot link; resend verification (`email/resend`); verify link; SSO tab with sign-in ID (`sso/{slug}/start`) | KEEP |
| `/forgot` | Email, send link (`password/reset/request`), sent state | KEEP |
| `/reset` | Token from the hash, new and confirm password (`password/reset`), done state | KEEP |
| `/verify-email` | Token from the hash (`email/verify`), result | KEEP |
| `/` Home | Question box (chat stream), since you last looked (`signals`), cap meter (`me`, `accounts`) | KEEP |
| `/accounts` | Rows, search and sort (pure UI), add account (`POST accounts`), cap state | KEEP |
| `/accounts/[domain]` | Header, refresh dialog (`POST runs`, `settings`), run progress and cancel, what we know (`tables/facts`), what changed (`history`), downloads (`POST exports`), composer | KEEP; BUILD pull option in Refresh, export history |
| `/accounts/[domain]/tables/[table]` | Five tables (people, jobs, tech, facts, coverage); search, filters and sort (server spec); saved views (`GET/POST views`); paging; source drawer; copy into a note and usefulness (`POST usefulness`); row feedback (`POST feedback`); CSV per table (`POST exports`, in the old account page) | KEEP; BUILD view rename and archive |
| `/accounts/[domain]/history` | Expired signals (`history`) | KEEP (as the Signals section) |
| `/chat` | Threads list, open, delete, stream, stop, copy, download | KEEP |
| `/alerts` | Inbox, lead banner, rate (`alerts/{id}/feedback`) | KEEP; BUILD usefulness summary (admin) |
| `/runs` | Runs list with paging, search and status filter on the page | KEEP |
| `/runs/[run_id]` | Status, progress, outcome, approve (`approve`), cancel (`cancel`), coverage results | KEEP; BUILD spend cap decision and live work counts |
| `/settings` | Research preferences (`PUT settings`), branding (`PUT branding`, logo upload) | KEEP |
| `/settings/users` | Members table, add, role change, suspend, reactivate, remove (`users`), sign out everywhere (`logout-all`) | KEEP |
| `/settings/security` | Password enrolment, sessions list and revoke | KEEP |
| `/settings/sso` | Connections table | KEEP; BUILD create and edit |
| `/settings/alerts` | Digest, immediate rules, chat-app delivery, threshold (`PUT subscriptions`), subscribe all, monitoring allowance | KEEP |
| `/settings/connectors` | Connect three sources (`POST connectors`), list, review held items and clear, disconnect | KEEP; BUILD allowed folders (`grants`) and fetch now |
| `/settings/crm` | CRM picker, enable, revoke, mapping, preview, request, approve, writes table | KEEP |
| `/settings/support` | Grant, grants table and revoke, support actions | KEEP |
| `/settings/model-key` | Status, save, remove | KEEP |
| `/usage` | Usage table | KEEP; BUILD CSV download |
| `/audit` | Events table, CSV download | KEEP |
| `/design` | Old component catalogue, public in production | DELETE (replaced by the dev-only `/__design`) |
| (new) `/feedback` | Feedback list (`GET feedback`), usefulness metric | BUILD |
| (new) `/downloads` | Export packages list and files (`GET exports`) | BUILD |

### Operator

| Route | Elements and operations | Decision |
|---|---|---|
| `/operator/login` | Google sign-in through `/api/operator/login` (backend-verified), error messages by code | KEEP |
| `/operator` | KPI tiles (`tenants`), tenant table, flags and keys summary, pilot signals (`pilot/aggregate`) | KEEP |
| `/operator/tenants` | Provision (`POST tenants`), lifecycle, plan, quota forms | KEEP; BUILD deletion flow |
| `/operator/configuration` | Add flag version, add key reference, tables | KEEP |
| `/operator/support` | Grant lookup, accounts and settings through a grant | KEEP |
| `/operator/runs` | Grant and run lookup, timeline | KEEP; BUILD pre-pulled upload |
| `/operator/cost` | Grant and run lookup, cost lines, reconciliation | KEEP |
| `/operator/spend` | Grant and run lookup, live spend | KEEP |
| `/operator/failures` | Grant lookup, failures, stuck runs | KEEP |
| `/operator/review` | Placeholder, no operation | DELETE |
| `/operator/monitors` | Placeholder, no operation | DELETE |
| `/operator/audit` | Placeholder, no operation | DELETE |
| `/operator/announcements` | Placeholder, no operation | DELETE |

## 3. Notes for the owner

- **Client roles:** the backend still has four client roles (admin, analyst, seller, viewer),
  and the guard enforces them. The UI mirrors the server: admin-only pages say so. A flat
  client role is a backend change and is not made here.
- **Spend cap amounts:** cap approval shows a dollar amount, because an approval must name its
  consequence. `CapProposalView.cap_micros` is returned to clients by the backend. If client
  screens must never show money, the endpoint should stop returning it.
- **Removed screens:** the screens the build prompt lists with no backend (Schedule, the 6th
  Library, per-user Connections, idle expiry, scoped refresh estimates, operator Usage charts,
  Library upload, Platform defaults and View as a client) are not built. They are listed in
  `docs/FRONTEND_BACKEND_GAPS.md`.

## 4. DELETE re-read

I re-read the DELETE list once, looking for anything that works under another operation name:

- The four operator placeholders have no operation in the schema, and their pages say so.
- The operator audit store has no read endpoint.
- `/design` is a static catalogue.

Nothing on the list is backed by a working operation.
