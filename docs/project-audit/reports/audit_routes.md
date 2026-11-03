# Backend route inventory: Account Intelligence API

Audit date 2026-11-03. Read-only. Repo `/home/sakib/ABM Generator`, branch `feat/final-product`, HEAD `d3831549`.
Method: route decorators were extracted by parsing every `src/account_intelligence/api/routes/*.py` with Python's `ast` (no import, nothing run); the policy catalogue was rebuilt the same way from `api/policy.py` plus every `register(...)` call and then compared with the route list; web callers were found by static grep of `web/app`, `web/components`, `web/lib`, `web/ui` (generated client, tests and e2e excluded). Anything not confirmed by reading code is marked "unverified".

## 1. Totals

| Item | Count | Source |
|---|---|---|
| Route handlers found (all `@router.get/post/put/patch/delete`) | **234** | AST of `api/routes/*.py`; no `add_api_route`, `mount` or websocket anywhere in `src/` |
| Probes (`/healthz`, `/readyz`) | 2 | `routes/health.py` |
| Worker task routes (`/tasks/*`) | 13 | `routes/tasks.py` |
| Catalogue-guarded routes (`/v1/*` and `/operator/v1/*`) | 219 | exactly equals the number of `ROUTE_POLICIES` entries (no route without entry, no entry without route) |
| ... of which tenant plane, signed-in | 160 | |
| ... of which anonymous sign-in routes | 10 | `/v1/auth/*` (6) and `/v1/sso/*` (4) |
| ... of which operator (staff) plane | 49 | |
| OpenAPI snapshot `docs/api/openapi.json` | 221 operations | = 219 + 2 probes; `/tasks/*` deliberately left out (`scripts/export_openapi.py:39`) |
| Tenant-plane routes with no web caller found | 73 | static grep; see section 14 item 2 |
| Operator routes the staff console actually calls | 26 of 49 | the rest are API-only |

Roles (from `config/roles.yaml`): `admin`, `analyst`, `seller`, `viewer`. Catalogue role sets seen: all 4 roles (77 routes), admin only (49), admin+analyst+seller (33), admin+analyst (1). 27 tenant routes also demand a fresh MFA step-up.

## 2. How a request is built: files and layers

| File | Role |
|---|---|
| `api/main.py` | ASGI entry (`uvicorn account_intelligence.api.main:app`), lifespan, middleware, exception handlers, sweeper loop |
| `api/routes/__init__.py` | builds four routers: `v1_router`, `public_v1_router`, `operator_router`, plus `tasks.router` and `health.router` mounted bare |
| `api/policy.py` | the default-deny route catalogue `ROUTE_POLICIES` (39 entries written inline; the other 180 are added by `register(...)` at import time in each route module) |
| `api/guard.py` | `route_guard` (catalogue lookup, 403 if absent) and `authorize` (role, membership, CSRF, step-up, read audit) |
| `api/auth.py` | `identity()` resolves a session cookie or bearer JWT; `require_role`, `check_membership` |
| `api/operator.py` | staff principals: `staff`, `require_staff`, `require_operator` (support grant) |
| `api/membership.py` | 60 s per-process membership cache |
| `api/tenant_plane.py` | `TenantPlane` (IdP port, secrets, object store, mailer, scanner, clock, session policy); CSRF key ref `csrf-signing-key` |
| `api/audit_log.py` | `audited_txn`, `audit_action`, read-event writer |
| `api/ratelimit.py`, `identity/rate_limit.py` | token buckets (section 6) |
| `api/errors.py`, `api/problem.py` | `APIProblem`, `DependencyUnavailable`, `problem_body` (section 8) |
| `api/contract.py` (3123 lines) | only response models (pydantic, `extra="forbid"`); the typed contract the OpenAPI snapshot and the web client are generated from. No logic. |
| `api/idempotency.py`, `etag.py`, `pagination.py`, `pricing.py`, `domains.py`, `ports.py`, `team_invites.py` | helpers (hash of a request body, ETag, keyset cursors, domain validation, invite rules). Not routes. |
| `routes/file_runs.py` | not a router; `create_file_run()` called by `POST /v1/runs` for input mode "file" |

## 3. App start-up (container, sweeper wiring)

One image serves three Cloud Run services (`infra/stack/main.tf` ~L560-640, `docker/api.Dockerfile`): `api` (`SERVICE_ROLE=api`, ingress internal, reached by the web service over VPC egress), `worker` (`SERVICE_ROLE=worker`, ingress internal, CPU always on, clamd sidecar, `--workers 1`, audience = tasks audience) and `web` (Next.js, public only through the HTTPS load balancer with Cloud Armor). Both Python services run `uvicorn account_intelligence.api.main:app`, so **every route exists on both**; what differs is ingress, secrets and the sweeper (the worker mounts provider keys; the api does not).

Lifespan order (`main.py:lifespan`):
1. `load_settings()` validates the whole environment (fail fast); `configure_logging`.
2. In the cloud with `METRICS_EXPORT=cloud_monitoring`: metrics exporter and Cloud Trace flush loop.
3. `stalldump.install()` (a blocked event loop dumps every thread stack).
4. `app.state.draining=False`; `build_container()` (`app/container.py`, the single wiring point). `APP_ENV` `dev`/`test` = hermetic adapters (in-process scheduler, fake model, env secrets); `staging`/`production` = Google adapters only (Cloud Tasks, GCS, Secret Manager, Cloud SQL connector, Claude via Vertex or Anthropic API). `assert_gcp_runtime` (called by `/readyz`) refuses hermetic adapters in the cloud.
5. In cloud api role: `assert_signing_keys` (CSRF key must exist in Secret Manager).
6. `replica.jobs.configure(scheduler, engine)`; chat service plus per-provider chat clients (Anthropic, Moonshot/Kimi, xAI/Grok) on `app.state.chat_service` / `chat_providers`; `TenantModelKeyStore` (KMS) on `app.state.tenant_model_keys`.
7. `bind_cloud_engine(settings)` (Cloud SQL connector engine with IAM auth, bound into `tenant_txn`).
8. SIGTERM handler flips `/readyz` to 503 (`draining`) then calls uvicorn's own handler; Cloud Tasks redelivers unfinished slices.
9. **Sweeper**: only if `SERVICE_ROLE=worker` and `RUN_SWEEPER=on` (default on): `asyncio.create_task(_sweep_loop)` runs `sweep_pass` at startup and then every `SWEEP_INTERVAL_S` (default 300) as a system job (`set_system()`).

`sweep_pass` (also run by `POST /tasks/sweep` from Cloud Scheduler every 5 min, because the worker can scale to zero) does six independent steps, each wrapped so one failure never stops the next: (1) re-enqueue documents stuck mid scan/parse/index and back-fill deliverable imports; (2) end orphaned runs (lease expired for `ORPHAN_AFTER_S`=600 s) as PARTIAL `worker_lost`; (3) mark stalled runs; (4) `replica.auto_resume` continues stalled/lost research within platform bounds; (5) settle research jobs whose run has ended (`settle_once`); (6) `runs.recovery.recover` restarts anything committed but never handed to the worker (queued jobs, runs that never began slice 0, expired waits).

Docs UI (`/docs`, `/redoc`, `/openapi.json`) are disabled when `APP_ENV` is staging or production.

## 4. Middleware stack and what runs per request

There is exactly **one** HTTP middleware (`@app.middleware("http") request_context`, `main.py`). No CORS middleware exists (the browser only talks to the same-origin web BFF), no GZip, no TrustedHost. Uvicorn runs with `--proxy-headers --no-access-log` on the api image.

`request_context`: keeps a UUID `X-Request-ID` from the BFF or makes one, stores it in `request.state` and the logging contextvar; times the call; records `api_requests_total` / `api_latency_ms` by bounded route class (`v1_runs`, `v1_other`, `operator`, `tasks`, `probe`); writes one allow-listed `http.request` log line (method, route template, status, latency, request id; never query string, headers or body; probes skipped); sets on every response `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, and HSTS when the request came over HTTPS (direct or `X-Forwarded-Proto`).

Routers and their dependency chains:

| Router (prefix) | Dependencies | What the dependency chain does |
|---|---|---|
| `v1_router` (`/v1`, ~35 route modules) | `route_guard`, then `authorize` | catalogue lookup then full principal check; handlers then add their own `Depends(authorize)` (cached) or `require_role(...)` |
| `public_v1_router` (`/v1`: `sso.public_router`, `auth.public_router`) | `route_guard` only | catalogue must hold an `ANONYMOUS` entry; no principal; handlers do their own same-origin and rate-limit checks |
| `operator_router` (`/operator/v1/...`: spend_v2 operator, ops, library operator, operator_admin, admin_console, operator_view, feedback operator) | `route_guard` only | each handler depends on `require_staff` or `require_operator` (staff JWT, section 5.4) |
| `tasks.router` (`/tasks`) | none (not in the catalogue) | each handler calls `_authorise` (Google OIDC token, section 5.5) |
| `health.router` | none | public probes |

## 5. Auth, CSRF, sessions

### 5.1 Principals (`api/auth.py`)
`identity()` accepts one of two credentials on tenant routes: (a) `Authorization: Bearer <JWT>` verified RS256 against `JWKS_URL`, issuer, audience, requires `exp, iat, sub, tenant_id, role`; or (b) the cookie `__Host-ai_session`. A bearer header wins if present. The browser path is always the cookie. For a cookie the SQL definer function `session_tenant(token_hash)` finds the tenant (the DB stores only the SHA-256 of the 256-bit token), then, in the same transaction bound to that tenant, `sessions.load` and `membership.active_role` fetch the session and the member's STORED role (the role is never taken from the cookie).
`authorize()` (router dependency) then: refuses anonymous-type policies; requires `ident.kind` in the policy's principal types and `ident.role` in the catalogue roles (403); re-checks membership via `MembershipCache` (about 60 s, per process, fail closed 503 on DB error, 401 if removed/suspended/re-roled or tenant suspended); enforces CSRF for cookie sessions on non-GET/HEAD/OPTIONS; enforces step-up; sets `request.state.principal`; calls `set_actor(user)` and `set_user_scope()` (private-workspace row scoping); and for `audit == "read"` writes the deduplicated read audit event BEFORE any data is returned (fail closed unless `audit.read_fail_closed` says otherwise). Handler-level `require_role` is an additional check; the effective permission is the intersection (for example `spend.py` cap routes say `admin, seller` in the handler but the catalogue says admin only, and the catalogue wins).

### 5.2 CSRF (`identity/csrf.py`)
Token = HMAC-SHA256(server key, `csrf:<session id>`) hex. The browser reads it from `GET /v1/sso/session` (`csrf_token`) and sends header `X-CSRF-Token` on every unsafe method; missing/wrong gives 403 `csrf_refused` (the web client re-reads the session once and retries). Bearer-JWT callers are exempt. The key lives in Secret Manager (`csrf-signing-key`; `CSRF_KEY_SECRET` env). Cookie flags: `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, `__Host-` prefix (so no Domain). The pre-auth sign-in cookie `__Host-ai_preauth` is `SameSite=None` for SAML, `Lax` for OIDC. In addition the public password routes call `_require_same_origin` (Origin header must equal the configured public base URL).

### 5.3 Session policy (`config/session.yaml`, marked `status: proposal`)
Idle timeout 1800 s, absolute 28800 s (8 h), `last_seen` touch every 60 s, step-up window 600 s, `admin_requires_mfa: true`, membership cache 60 s, download token TTL 300 s. Sign-in and privilege changes rotate the token. Role change or suspension ends the member's sessions.
Consequence read from code: local-password login (`POST /v1/auth/login`) is refused (generic 401) for any `admin` while `admin_requires_mfa` is true because no TOTP verifier is wired; admins can therefore only enter through client SSO with MFA, and operator-invited first users are given role `analyst` (`admin_console.py` `INVITED_ROLE`).

### 5.4 Operator plane (`api/operator.py`)
Staff authenticate with a bearer ID token from the 6th staff IdP (Google in staging: `STAFF_JWT_ISSUER=https://accounts.google.com`). Role (`operator` or `support`) comes only from server-side email allow-lists (`STAFF_OPERATOR_EMAILS`, `STAFF_SUPPORT_EMAILS`, `STAFF_EXTERNAL_EMAILS`) plus workspace domain; an IdP `staff_role` claim counts only if `STAFF_IDP_ASSERTS_ROLE` is on. In dev/test without staff env vars a `DevStaffVerifier` accepts tokens like `operator:alice` (never in the cloud: unset env there gives 503).
* `require_staff`: catalogue `operator_roles` must contain the role, and a fresh MFA/re-auth within the 600 s window is required on EVERY staff call including reads (403 "step-up required"); runs the request in system scope (`set_system()`); returns names and counts only.
* `require_operator`: used where tenant data is touched. Needs header `X-Support-Grant: <uuid>`, an active time-boxed grant a TENANT ADMIN created for THIS operator (`POST /v1/support/grants`); read grants allow safe methods only; every call, allowed or refused, writes a `support_session_events` row and a tenant audit row in that tenant (fail closed 503 if it cannot).
* Tenant source on operator routes: `operator_grant` (from the support grant), `operator_target` (operator names a tenant in the path, lifecycle/limits only, never a data read) or `none`.

### 5.5 Worker tasks (`routes/tasks.py:_authorise`)
`/tasks/*` carry ids only (`extra="forbid"` bodies) and require a Google-signed OIDC token for `TASKS_AUDIENCE` from the `TASKS_SERVICE_ACCOUNT` email (`platform/gcp/adapters.verify_task_token`), checked in a thread with a 20 s bound (a synchronous cert fetch once froze `/healthz`). In `dev`/`test` with no verifier the routes answer only if `ALLOW_UNAUTHENTICATED_TASKS=1`, else 403. After authentication the handler calls `set_system()`, then `_act_as_owner_of(kind, tenant, item)` which resolves the item's owner under RLS (404 if not in that tenant) and acts as that user.

## 6. Rate limits

| Layer | Where | Limit | Notes |
|---|---|---|---|
| Edge | Cloud Armor in `infra/modules/edge` (rule 2000) | `rate_limit_per_minute` requests per client IP, then 429 and a 300 s ban | value is a Terraform var (not read here) |
| Sign-in buckets | `identity/rate_limit.consume` on `auth/login`, `auth/password/enroll`, `auth/email/resend`, `auth/email/verify`, `auth/password/reset/request`, `auth/password/reset`, `auth/invite/accept`, `sso/discover` | `config/auth.yaml`: IP 30 burst / 0.5 per s; account 10 burst / 0.1 per s | Redis in the cloud (fail closed 503 if Redis is down); in-process buckets in dev. With Redis configured the IP bucket is SKIPPED in the API (all traffic arrives from the web service) and left to Cloud Armor. Keys are SHA-256 of the email/IP |
| Per-user money/compute buckets | `api.ratelimit.limit_user` -> `identity.rate_limit.consume_user`, config `config/rate_limits.yaml` | `document_upload` 120 burst, 30/min; `drive_list` 60, 30/min; `part_retry` 60, 12/min; `research_resume` 20, 3/min; `download_everything` 10, 10/h; `export` 30, 3/min; `archive_read` 120, 120/min; `maildrive_sync_google|microsoft` 1 per 2 min | 429 with `Retry-After`. Values are labelled "proposals, unmeasured" |
| Per-user monthly counts | `replica.limits` (`LIM.take`) | researches and chats per period, set per user by staff | 403 `limit_reached` |
| Chat thread | `chat.store` | turn/spend limit per conversation | 429 `chat_limit_reached` |
| Request body | web BFF `lib/proxy-body.ts` | 1 MiB JSON; 21 GiB for `PUT /v1/documents/{id}/content` | 413 |
| Idempotency | `Idempotency-Key` header (8-64 chars) on `POST /v1/runs`; `request_id` in body on research, refresh, ask, download-everything | same key + same body replays, other body 409 | |

## 7. Tenant isolation per request

1. The tenant is never a request field: it comes from the verified session/JWT (`Identity.tenant_id`). `scripts/check_route_catalog.py` fails the build if a `/v1` route declares a tenant parameter (operator `operator_target` routes may have `{tenant_id}` in the path only).
2. Every handler opens `async with tenant_txn(ident.tenant_id) as s:` (`db/session.py`). That starts one transaction and runs `set_config('app.tenant_id', ..., true)`, `app.user_id` and `app.scope` (`user` or `system`) with `is_local=true`, so the setting dies with the transaction and a pooled connection cannot carry tenant A into B. `tenant_txn(snapshot=True)` makes it `REPEATABLE READ, READ ONLY` (export builders).
3. Postgres Row Level Security does the enforcement: tables have `ENABLE` + `FORCE ROW LEVEL SECURITY` with policy `tenant_id = (SELECT app_tenant())`, the app connects as role `app_rw` (`NOBYPASSRLS`, owns nothing; migrations use a separate migrator role). Unset tenant gives NULL and zero rows. `public_reader` is a switched role for public rendering only. 195 migration files; `EXPECTED_REVISION = "0097"` in `routes/health.py` (readiness fails while the DB is older).
4. Private workspaces (migration 0063, `config/ownership.yaml`): owned tables are additionally restricted to the acting user unless the scope is `system`. `authorize` sets actor + user scope; staff routes, the sweeper and scheduler ticks run as `system`. With neither actor nor system, owned rows are invisible and inserts fail.
5. `untenanted_session()` (no tenant context, RLS shows zero rows) is used only for SECURITY DEFINER identity lookups: `session_tenant`, `idp_connection_tenant`, `idp_connection_for_domain`, `login_state_tenant`, `local_auth_*`, `operator_tenant_overview()` (tenant ids for cross-client ticks), `maildrive_connections_due`, and the operator tenant/library admin queries.
6. Object-store paths are tenant checked (`tenancy.storage.require_owned`) before any download.
7. A nightly `isolation-check` task (`/tasks/isolation-check`) probes structure, every tenant table and the read routes from a dedicated probe client and records the result.

## 8. Error handling conventions

All errors are `application/problem+json` with `{type, title, status, detail, request_id}` (`type` = `urn:6th:problem:<code>`).
* `APIProblem(status, type, title, detail, headers?, extra?)` (api/errors.py): expected, safe errors with their own stable type (for example `estimate_changed`, `credits_short`, `account_cap_reached`, `csrf_refused`, `step_up_required`, `idempotency_key_reuse`, `rate_limited`); optional `context` object.
* Framework `HTTPException`: body type is `urn:6th:problem:<status>`; the handler keeps `detail` ONLY if it is exactly `invalid token`, `forbidden` or `not found`; any other message is replaced by the status title (so strings like the login "Email or password is incorrect..." never leave the server; the client shows its own words).
* `RequestValidationError` gives 422 with `errors[{loc,msg,type}]` (max 50) and never echoes submitted values.
* `DependencyUnavailable` and platform `NotConfigured` give 503 (fail closed, never a fake success); `PayloadMissing` gives 409 `source_missing`; `AccountCapReached` gives 409 `account_cap_reached` with `context.cap`; anything else gives 500 with no exception text (logged with request id).
* Deviation: `POST /v1/auth/login` returns `409 {"code":"tenant_selection_required","tenants":[...]}` in a bespoke (non-problem) shape when a user belongs to several workspaces.
* Server-sent-event routes (`/chat/ask`, `/chat/threads/{id}/turns/stream`, `/runs/{id}/events`, `/research/live/stream`) report failures as an `error` event inside the stream once headers are sent.

## 9. The web tier in front of the API (context for "called by")

* `web/app/v1/[[...path]]/route.ts`: catch-all same-origin proxy of `/v1/*` to `API_ORIGIN`. Non-GET requires `Origin` equal to the request host (`isSameOriginRouteRequest`) else 403. It forwards only a header allow-list (`accept, content-type, idempotency-key, if-none-match, if-range, last-event-id, origin, range, traceparent, x-csrf-token, x-forwarded-for`) and only the three auth cookies (`__Host-ai_session`, `__Host-ai_preauth`, `__Host-ai_preauth_once`); streams bodies with a size cap; 120 s timeout (60 min for file transfers); SSE passes through.
* `web/app/api/operator/v1/[...path]/route.ts`: staff proxy. Only operations present in the generated client with `plane == "operator"` are forwarded; it reads the staff session cookie and sends it as `Authorization: Bearer`, plus `X-Support-Grant`. Staff sign-in: `app/api/operator/login|callback|session` (Google OIDC).
* `web/app/api/auth/logout/route.ts` calls `GET /v1/sso/session` then `POST /v1/sso/logout` server-side.
* `web/app/healthz`, `web/app/readyz` are the web's own probes (`/readyz` calls the API `/readyz` with a 2 s timeout).
* Server Components call the API through `serverGet` (`web/lib/server-api.ts`) with the user's cookies.

## 10. Legend for the route tables

* **Method / Path**: full path as mounted. `{x}` are path parameters.
* **Handler**: `routes/<file>:<line> <function>`.
* **Who may call**: from the catalogue (`api/policy.py`): the roles allowed, then `fresh MFA step-up` if the route's role-action is in `step_up` (`config/roles.yaml`: manage_users, manage_sso, manage_connectors, export_audit, grant_support, request_deletion, approve_cap, manage_settings), then the role-action name in `[...]` (informational). `all 4 roles` = admin, analyst, seller, viewer. Staff rows say operator/support and the tenant source.
* **Audit**: audit class (`read` writes a deduplicated read event before the response; `run`, `export`, `admin` are written by the handler in its own transaction; `none`).
* **Tables / notes**: tables named in the handler body by a regex over its SQL (not exhaustive; most reads go through service modules). 
* **Called by**: `web:` = a static string match in the Next.js source; `no web caller found` = none found (it can still be called by API clients, other callers or dynamically built URLs: unverified); staff and scheduler callers as stated.
* Responses carry the model named in `api/contract.py`; request bodies forbid unknown fields.

## 11. Route tables (all 234 routes)

### Probes

| Method | Path | Handler | Who may call (catalogue) | Audit | What it does | Tables / notes | Called by |
|---|---|---|---|---|---|---|---|
| GET | `/healthz` | `routes/health.py:31` healthz | none (public probe) | - | Liveness: the process answers. |  | Cloud Run probes, web readyz |
| GET | `/readyz` | `routes/health.py:139` readyz | none (public probe) | - | Readiness that fails closed (W5): 200 only when every check passes, else 503 naming it. |  | Cloud Run probes |

### Auth, sessions and identity

| Method | Path | Handler | Who may call (catalogue) | Audit | What it does | Tables / notes | Called by |
|---|---|---|---|---|---|---|---|
| POST | `/v1/auth/login` | `routes/auth.py:136` login | anonymous (public) | none | Verify a password and establish one tenant-bound server-side session. | local_auth_failure, local_auth_lookup, local_auth_memberships | web: app/(auth)/login/page.tsx |
| POST | `/v1/auth/password/enroll` | `routes/auth.py:345` enroll_password | all 4 roles [manage_own_auth] | admin | Start password enrollment for an already authenticated tenant member. | users | no web caller found (API only) |
| POST | `/v1/auth/email/resend` | `routes/auth.py:406` resend_verification | anonymous (public) | none | Resend a pending address verification with a generic response for unknown or active users. | local_auth_lookup | web: app/(auth)/login/page.tsx |
| POST | `/v1/auth/email/verify` | `routes/auth.py:438` verify_email | anonymous (public) | none | Consume one live email-verification token; replay and expiry are generic refusals. |  | web: app/(auth)/verify-email/page.tsx |
| POST | `/v1/auth/password/reset/request` | `routes/auth.py:458` request_password_reset | anonymous (public) | none | Send a reset link when the address is eligible, with an enumeration-resistant response. | local_auth_lookup | web: app/(auth)/forgot/page.tsx |
| POST | `/v1/auth/password/reset` | `routes/auth.py:510` reset_password | anonymous (public) | none | Consume one reset token, replace the Argon2id hash and revoke active sessions. | local_auth_memberships | web: app/(auth)/reset/page.tsx |
| POST | `/v1/auth/invite/accept` | `routes/auth.py:549` accept_invite | anonymous (public) | none | Consume one invite token (from the link an operator copied in Add client), set the chosen password and activate the identity; the invited person then signs in as usual. |  | web: app/(auth)/invite/page.tsx |
| GET | `/v1/me` | `routes/me.py:52` current_user | all 4 roles [read_public] | read | Return only the active session's tenant and capability metadata. | accounts, feature_flags, tenants, users | web: app/(client)/layout.tsx + |
| POST | `/v1/sso/discover` | `routes/sso.py:94` discover | anonymous (public) | none | Find the company's sign-in from a work email's domain, without saying who exists. |  | web: app/(auth)/login/page.tsx |
| GET | `/v1/sso/{slug}/start` | `routes/sso.py:107` start | anonymous (public) | none | Open a sign-in and redirect to the tenant's IdP. |  | browser redirect (sign-in page links) |
| GET | `/v1/sso/oidc/callback` | `routes/sso.py:155` oidc_callback | anonymous (public) | none | Finish an OIDC sign-in. |  | browser redirect from IdP |
| POST | `/v1/sso/saml/acs` | `routes/sso.py:177` saml_acs | anonymous (public) | none | Finish a SAML sign-in (HTTP-POST binding). |  | IdP form POST (see note on BFF) |
| GET | `/v1/sso/session` | `routes/sso.py:199` whoami | all 4 roles [read_public] | none | The signed-in user, their role, the CSRF token for this session and whether step-up is fresh. |  | web: app/api/auth/logout/route.ts + |
| GET | `/v1/sso/sessions` | `routes/sso.py:215` list_sessions | all 4 roles [read_public] | read | List active sessions for this user in this tenant. |  | no web caller found (API only) |
| DELETE | `/v1/sso/sessions/{session_id}` | `routes/sso.py:238` revoke_session | all 4 roles [revoke_own_session] | none | Revoke one of the caller's sessions without revealing other users' session ids. |  | no web caller found (API only) |
| POST | `/v1/sso/logout` | `routes/sso.py:258` logout | all 4 roles [revoke_own_session] | none | End this session (cookie sessions only). |  | web: app/api/auth/logout/route.ts |
| POST | `/v1/sso/logout-all` | `routes/sso.py:271` logout_all | all 4 roles [revoke_own_session] | none | End every session of this user ("sign out everywhere"). |  | no web caller found (API only) |
| GET | `/v1/sso/connections` | `routes/sso.py:318` list_connections | admin only [manage_sso] | read | The tenant's IdP connections (secrets are never returned, only whether one is set). |  | no web caller found (API only) |
| POST | `/v1/sso/connections` | `routes/sso.py:334` create_connection | admin only; fresh MFA step-up [manage_sso] | admin | Create an IdP connection (admin with a fresh step-up). |  | no web caller found (API only) |
| PUT | `/v1/sso/connections/{connection_id}` | `routes/sso.py:363` update_connection | admin only; fresh MFA step-up [manage_sso] | admin | Update a connection (conditional on its version); a new secret replaces the reference. |  | no web caller found (API only) |

### Research jobs (credits, live view, refresh, schedules)

| Method | Path | Handler | Who may call (catalogue) | Audit | What it does | Tables / notes | Called by |
|---|---|---|---|---|---|---|---|
| GET | `/v1/accounts/{domain}/refresh` | `routes/credits.py:114` refresh_options | all 4 roles [read_public] | read | What a web refresh of this account would cost, before anything is confirmed. |  | web: app/(client)/accounts/[domain]/refresh/page.tsx + |
| POST | `/v1/accounts/{domain}/refresh` | `routes/credits.py:152` confirm_refresh | admin, analyst, seller [start_run] | run | Reserve the credits the user saw and start the refresh. |  | web: app/(client)/accounts/[domain]/refresh/page.tsx + |
| GET | `/v1/research/live` | `routes/live.py:74` research_live | all 4 roles [read_public] | read | Every web research running now and finished recently, and the selected one in detail. |  | web: lib/useResearchLive.ts |
| GET | `/v1/research/live/stream` | `routes/live.py:85` research_live_stream | all 4 roles [read_public] | read | The snapshot as server-sent events (see the module docstring). |  | web: lib/useResearchLive.ts |
| GET | `/v1/research/estimate` | `routes/research.py:81` research_estimate | all 4 roles [read_public] | read | What researching a new company would cost, before anything is confirmed. |  | web: app/(client)/accounts/add/page.tsx + |
| POST | `/v1/research` | `routes/research.py:118` start_research | admin, analyst, seller [start_run] | run | Add the company for the caller, reserve the credits they saw and start the research. |  | web: components/accounts/ResearchCompany.tsx |
| POST | `/v1/research/resume` | `routes/research.py:231` resume_research | admin, analyst, seller [start_run] | run | Continue a stopped research (owner decision 2026-10-14, Phase 4B section 4): a new run on the same account, scope, topic and models that reuses every finished pull and model reply of the stopped run a | account_runs, accounts | web: components/accounts/ResumeResearch.tsx |
| POST | `/v1/research/{job_id}/stop` | `routes/research.py:315` stop_research | admin, analyst, seller [start_run] | run | Stop the research (see the module docstring); 409 when it has already ended. | research_jobs | web: components/accounts/ResearchProgress.tsx + |
| GET | `/v1/schedules` | `routes/schedules.py:34` schedules | all 4 roles [read_public] | read | Every account's schedule and the monthly estimate. |  | web: app/(client)/schedules/page.tsx |
| PUT | `/v1/schedules/{account_id}` | `routes/schedules.py:48` save_cadence | admin, analyst, seller [start_run] | run | Save the cadence now and return the updated view. | accounts | web: components/accounts/SchedulesView.tsx |

### Runs and spend caps

| Method | Path | Handler | Who may call (catalogue) | Audit | What it does | Tables / notes | Called by |
|---|---|---|---|---|---|---|---|
| GET | `/v1/runs/{run_id}/internal` | `routes/internal_view.py:32` internal_view | admin, analyst, seller | read | See the module docstring. | account_runs, facts, internal_deliverables | web: components/ws/FileAnalysis.tsx |
| GET | `/v1/runs/{run_id}/internal/file` | `routes/internal_view.py:94` internal_file | admin, analyst, seller | export | The Internal Only Word file (an export audit event). | internal_deliverables | web: components/ws/FileAnalysis.tsx |
| POST | `/v1/runs` | `routes/runs.py:114` create_run | admin, analyst, seller | run | Create a run (both rows, reservation, tracker in one transaction) and enqueue slice 0. |  | web: components/accounts/NewResearch.tsx |
| GET | `/v1/runs` | `routes/runs.py:349` list_runs | all 4 roles [read_public] | read | The tenant's runs, newest first (keyset by creation time and id), with the client status label. | account_runs, accounts | web: components/accounts/NewResearch.tsx |
| GET | `/v1/runs/pull-option` | `routes/runs.py:396` get_pull_option | all 4 roles | read | Whether "Phase 2 only" may be offered for the account, with the pull's date and coverage (work item 2). |  | no web caller found (API only) |
| GET | `/v1/runs/{run_id}` | `routes/runs.py:422` get_run | all 4 roles | read | The run's status, end reason and client-safe coverage view (leak-scanned). | account_runs, accounts, run_gate_results | web: components/accounts/NewResearch.tsx + |
| GET | `/v1/runs/{run_id}/collected` | `routes/runs.py:539` get_collected | all 4 roles | read | Everything the run holds so far (a PARTIAL run included): finished pulls by tool category (no provider is named), public graded facts, the client distill drafts, and the coverage rows still missing wi | abm_distill_outputs, account_runs, facts, tool_calls | no web caller found (API only) |
| POST | `/v1/runs/{run_id}/approve` | `routes/runs.py:645` approve | admin, analyst, seller | run | Approve a run waiting at the approval gate and enqueue its next slice. | account_runs | web: components/ws/RunSteps.tsx |
| POST | `/v1/runs/{run_id}/cancel` | `routes/runs.py:682` cancel | admin, analyst, seller | run | Cancel a run: it ends CANCELED, unset rows are swept and the spend so far is settled. |  | web: components/ws/RunSteps.tsx |
| GET | `/v1/runs/{run_id}/progress` | `routes/runs.py:761` run_progress | all 4 roles [read_public] | read | Stages done of stages (``3 of 7``), with an ETag for cheap polling. |  | web: components/ws/RunSteps.tsx + |
| GET | `/v1/runs/{run_id}/events` | `routes/runs.py:776` run_events | all 4 roles [read_public] | read | Server-sent progress events: one event per change, a heartbeat, a bounded duration (the client reconnects with Last-Event-ID and is authorised again). |  | web: lib/useRunStream.ts |
| GET | `/v1/runs/{run_id}/cap` | `routes/spend.py:70` current_cap | all 4 roles | read | The latest cap proposal of the run; 404 when none exists for this tenant. |  | web: components/ws/RunSteps.tsx |
| POST | `/v1/runs/{run_id}/cap/approve` | `routes/spend.py:81` approve_cap | admin only; fresh MFA step-up [approve_cap] | run | Approve the shown cap proposal; an AWAITING_APPROVAL run then resumes. | account_runs | web: components/ws/RunSteps.tsx |
| POST | `/v1/runs/{run_id}/cap/reject` | `routes/spend.py:138` reject_cap | admin only; fresh MFA step-up [approve_cap] | run | Reject a cap version (the run then has no approved cap: no paid call). |  | no web caller found (API only) |
| POST | `/v1/runs/{run_id}/cap/extend` | `routes/spend.py:175` extend_cap | admin only; fresh MFA step-up [approve_cap] | run | Request an extension (a new version that needs its own approval). |  | no web caller found (API only) |
| GET | `/v1/runs/{run_id}/spend` | `routes/spend.py:211` tenant_spend | all 4 roles | read | The tenant spend view of the latest report (categories and call counts only); the caller's own runs only (the run is checked under the owner policy before the reader role, whose policy on spend_report | account_runs, spend_reports | no web caller found (API only) |
| GET | `/v1/runs/{run_id}/spend/live` | `routes/spend_v2.py:101` tenant_live | all 4 roles | read | The tenant's live spend status (allow-listed). |  | web: components/ws/RunSteps.tsx |
| POST | `/v1/runs/{run_id}/cap/pauses/{request_id}/approve` | `routes/spend_v2.py:116` approve_pause | admin only | run | Approve a pause request (fresh step-up required); the run resumes. |  | no web caller found (API only) |
| POST | `/v1/runs/{run_id}/cap/pauses/{request_id}/decline` | `routes/spend_v2.py:171` decline_pause | admin only | run | Decline a pause request; the run resumes to end BUDGET_EXCEEDED with partial results. |  | no web caller found (API only) |

### Ask, chat and model choice

| Method | Path | Handler | Who may call (catalogue) | Audit | What it does | Tables / notes | Called by |
|---|---|---|---|---|---|---|---|
| POST | `/v1/chat/ask` | `routes/ask.py:152` ask | all 4 roles [read_public] | run | Answer one question from the workspace and stream the structured answer. | accounts, chat_threads | web: components/ask/Ask.tsx |
| GET | `/v1/models` | `routes/chat.py:124` models | all 4 roles [read_public] | read | The current allowlist and public costs; no provider credential is returned. |  | web: components/ask/ContextMeter.tsx |
| GET | `/v1/chat/provider-key` | `routes/chat.py:140` provider_key_status | admin only [manage_settings] | read | Return only whether a BYO key is set and when it was last changed. |  | no web caller found (API only) |
| PUT | `/v1/chat/provider-key` | `routes/chat.py:158` set_provider_key | admin only; fresh MFA step-up [manage_settings] | admin | Encrypt a tenant's optional Anthropic key with KMS; audit without its value. |  | no web caller found (API only) |
| DELETE | `/v1/chat/provider-key` | `routes/chat.py:190` remove_provider_key | admin only; fresh MFA step-up [manage_settings] | admin | Remove encrypted BYO key bytes and audit the content-free administrative action. |  | no web caller found (API only) |
| POST | `/v1/chat/threads` | `routes/chat.py:208` new_thread | admin, analyst, seller [start_run] | run | Create an empty private history owned by the verified user and tenant. |  | web: app/(client)/chats/page.tsx + |
| GET | `/v1/chat/threads` | `routes/chat.py:223` threads | all 4 roles [read_public] | read | List only this member's 100 most recently active threads. |  | web: app/(client)/chats/page.tsx + |
| GET | `/v1/chat/threads/{thread_id}` | `routes/chat.py:229` thread | all 4 roles [read_public] | read | Load a caller-owned conversation; other tenants and members receive a 404. |  | web: app/(client)/page.tsx + |
| DELETE | `/v1/chat/threads/{thread_id}` | `routes/chat.py:242` remove_thread | admin, analyst, seller [save_own_view] | none | Delete a caller-owned conversation and its stored transcript. |  | web: app/(client)/page.tsx + |
| POST | `/v1/chat/threads/{thread_id}/turns/stream` | `routes/chat.py:256` stream_turn | admin, analyst, seller [start_run] | run | Reserve a bounded turn before returning SSE, then persist token/cost attribution. |  | no web caller found (API only) |
| GET | `/v1/models/choices` | `routes/model_choice.py:54` choices | all 4 roles [read_public] | read | The model menus. |  | web: app/(client)/layout.tsx + |
| PUT | `/v1/me/model-preferences` | `routes/model_choice.py:62` save | admin, analyst, seller [save_own_view] | none | Remember the user's models. |  | web: components/ModelPreferences.tsx |

### Documents and uploads

| Method | Path | Handler | Who may call (catalogue) | Audit | What it does | Tables / notes | Called by |
|---|---|---|---|---|---|---|---|
| POST | `/v1/documents/uploads` | `routes/documents.py:143` start_upload | admin, analyst, seller | run | Declare a document and get its upload target (checked before any byte arrives). |  | web: lib/upload.ts |
| GET | `/v1/documents/drive-files` | `routes/documents.py:228` drive_files | admin, analyst, seller | read | The caller's newest files a research can read in ``source``, optionally by name. |  | web: components/ws/DriveChooser.tsx |
| POST | `/v1/documents/from-drive` | `routes/documents.py:257` import_from_drive | admin, analyst, seller | run | Fetch one of the caller's Google Drive or OneDrive files on the server into quarantine and enqueue its scan. |  | web: components/ws/DocumentDrop.tsx |
| PUT | `/v1/documents/{document_id}/content` | `routes/documents.py:308` upload_content | admin, analyst, seller | run | The local stand-in for a signed URL: the body streams into quarantine, never parsed. |  | web: lib/upload.ts only where store has no resumable session (dev) |
| POST | `/v1/documents/{document_id}/complete` | `routes/documents.py:340` complete | admin, analyst, seller | run | Check what arrived and enqueue the scan. |  | web: lib/upload.ts |
| GET | `/v1/documents/{document_id}/status` | `routes/documents.py:363` status | admin, analyst, seller | read | The document's status and detected company. |  | web: components/ws/DocumentDrop.tsx |
| POST | `/v1/documents/{document_id}/parts/{part}/retry` | `routes/documents.py:376` retry_part | admin, analyst, seller | run | Try a failed part again (``part`` counts from 1, as the screen shows it). |  | web: components/ws/DocumentDrop.tsx |
| GET | `/v1/documents/{document_id}/passages` | `routes/documents.py:406` passages | admin, analyst, seller | read | What was read from one document: its passages in order, each with where it came from (page, slide, row), a page at a time (audit 2026-10-15: the user could not see what a file contributed). | documents | web: components/accounts/WhatWasRead.tsx |
| GET | `/v1/documents` | `routes/documents.py:444` list_documents | admin, analyst, seller | read | The tenant's documents, newest first (one account's when given). |  | web: app/(client)/accounts/[domain]/page.tsx + |
| POST | `/v1/documents/confirm-company` | `routes/documents.py:454` confirm_company | admin, analyst, seller | run | Bind the documents to the confirmed company's account and index them. | accounts | web: components/accounts/NewResearch.tsx |
| POST | `/v1/documents/estimate` | `routes/documents.py:508` estimate | admin, analyst, seller | read | What a file-mode run over these documents would cost (shown before it starts). |  | no web caller found (API only) |
| DELETE | `/v1/documents/{document_id}` | `routes/documents.py:532` delete | admin, analyst, seller | run | Remove the document and everything derived from it; mark facts for re-evaluation. |  | web: components/accounts/DeleteFileButton.tsx + |
| GET | `/v1/documents/policy` | `routes/documents.py:555` get_policy | admin, analyst, seller | read | The tenant's redaction tier. |  | no web caller found (API only) |
| PUT | `/v1/documents/policy` | `routes/documents.py:567` put_policy | admin only; fresh MFA step-up [manage_settings] | admin | Set the redaction tier for documents parsed from now on (admin). |  | no web caller found (API only) |
| POST | `/v1/documents/{document_id}/authoritative` | `routes/documents.py:587` set_authoritative | admin only | admin | Mark a document an authoritative source (admin): later runs may grade its facts Confirmed; they stay private (Internal Only) all the same. |  | no web caller found (API only) |

### Mail/drive, connectors and CRM

| Method | Path | Handler | Who may call (catalogue) | Audit | What it does | Tables / notes | Called by |
|---|---|---|---|---|---|---|---|
| GET | `/v1/connectors` | `routes/connectors.py:82` list_connectors | admin only [manage_connectors] | read | The tenant's connections (never a token). | connector_connections, connector_grants, connector_quarantine | no web caller found (API only) |
| POST | `/v1/connectors` | `routes/connectors.py:123` start | admin only; fresh MFA step-up [manage_connectors] | admin | Start a consent for one source. |  | no web caller found (API only) |
| GET | `/v1/connectors/oauth/callback` | `routes/connectors.py:167` callback | admin only [manage_connectors] | admin | Finish a consent and return to the connectors page. |  | browser redirect from provider |
| POST | `/v1/connectors/{connection_id}/grants` | `routes/connectors.py:211` grant | admin only; fresh MFA step-up [manage_connectors] | admin | Allow one folder, object or field. |  | no web caller found (API only) |
| POST | `/v1/connectors/{connection_id}/fetch` | `routes/connectors.py:241` fetch | admin only [manage_connectors] | admin | One bounded fetch pass into quarantine (counts only; never content). |  | no web caller found (API only) |
| GET | `/v1/connectors/{connection_id}/quarantine` | `routes/connectors.py:281` held | admin only [manage_connectors] | read | Items surfaced outside the allow-list (identifiers and reasons only). |  | no web caller found (API only) |
| POST | `/v1/connectors/{connection_id}/quarantine/{item_id}/{decision}` | `routes/connectors.py:293` decide | admin only; fresh MFA step-up [manage_connectors] | admin | Clear (a later pass may read it) or refuse one held item. |  | no web caller found (API only) |
| POST | `/v1/connectors/{connection_id}/disconnect` | `routes/connectors.py:326` disconnect | admin only; fresh MFA step-up [manage_connectors] | admin | Revoke upstream where possible, purge the token, end the connection. |  | no web caller found (API only) |
| POST | `/v1/crm/{crm}/enable` | `routes/crm.py:115` enable | admin only; fresh MFA step-up [manage_connectors] | admin | Opt in. |  | no web caller found (API only) |
| POST | `/v1/crm/{crm}/revoke` | `routes/crm.py:133` revoke | admin only; fresh MFA step-up [manage_connectors] | admin | Stop writes at once. |  | no web caller found (API only) |
| POST | `/v1/crm/{crm}/mappings` | `routes/crm.py:151` confirm | admin only; fresh MFA step-up [manage_connectors] | admin | Confirm an account's CRM record. |  | no web caller found (API only) |
| POST | `/v1/crm/{crm}/preview` | `routes/crm.py:193` preview | admin only; fresh MFA step-up [manage_connectors] | admin | Exactly what would be created. |  | no web caller found (API only) |
| POST | `/v1/crm/{crm}/writes` | `routes/crm.py:216` request_write | admin only; fresh MFA step-up [manage_connectors] | admin | An idempotent write request (approval follows). |  | no web caller found (API only) |
| POST | `/v1/crm/{crm}/writes/approve` | `routes/crm.py:244` approve | admin only; fresh MFA step-up [manage_connectors] | admin | Approve a batch. | crm_writes | no web caller found (API only) |
| GET | `/v1/crm/{crm}/writes` | `routes/crm.py:278` writes | admin only [manage_connectors] | read | Every write and its events. | crm_write_events, crm_writes | no web caller found (API only) |
| GET | `/v1/mail-and-drive` | `routes/maildrive.py:166` mail_and_drive | all 4 roles [read_public] | read | The caller's sources, grouped by family, and the Mail and Drive cards. |  | web: app/(client)/mail-and-drive/page.tsx + |
| POST | `/v1/mail-and-drive/{kind}/connect` | `routes/maildrive.py:191` connect | all 4 roles [read_public] | admin | Start the read-only consent for one source. |  | web: components/accounts/MailDrive.tsx |
| GET | `/v1/mail-and-drive/callback` | `routes/maildrive.py:301` callback | all 4 roles [read_public] | admin | Finish a consent (bound to this user) and return to the page. |  | browser redirect from Google/Microsoft |
| POST | `/v1/mail-and-drive/{family}/sync` | `routes/maildrive.py:345` sync_now | all 4 roles [read_public] | admin | Sync the caller's own active connections of one family now. |  | web: components/accounts/MailDrive.tsx |
| POST | `/v1/mail-and-drive/{family}/disconnect` | `routes/maildrive.py:393` disconnect | all 4 roles [read_public] | admin | Revoke, destroy the token and delete what the caller's connection synced (``mail``, ``drive`` or one source kind). |  | web: components/accounts/MailDrive.tsx |

### Accounts, workspace, parity bundle, library, saved views

| Method | Path | Handler | Who may call (catalogue) | Audit | What it does | Tables / notes | Called by |
|---|---|---|---|---|---|---|---|
| GET | `/v1/accounts/{domain}/workspace` | `routes/account_workspace.py:270` account_workspace | all 4 roles [read_public] | read | The Account workspace screen for one account. |  | web: app/(client)/accounts/[domain]/page.tsx |
| GET | `/v1/accounts/{domain}/file-analysis` | `routes/account_workspace.py:333` file_analysis | all 4 roles [read_public] | read | The account's file analysis (the given run, else the latest file-mode run): its state, when it ran, how many files it read and whether public sources were added. | account_runs, coverage_rows, run_documents | web: app/(client)/accounts/[domain]/files/page.tsx |
| GET | `/v1/accounts/{domain}/deliverables` | `routes/account_workspace.py:376` account_deliverables | all 4 roles [read_public] | read | The account's current deliverables (newest version of each). |  | no web caller found (API only) |
| GET | `/v1/accounts/{domain}/deliverables/download` | `routes/account_workspace.py:388` download_deliverables | all 4 roles [export_files] | export | One file as itself, or several as one zip named after the account. | deliverables | web: app/(client)/accounts/[domain]/[sheet]/page.tsx + |
| GET | `/v1/accounts/{domain}/sheets/{sheet}` | `routes/account_workspace.py:485` account_sheet | all 4 roles [read_public] | read | One page of a deep-dive table, filtered and sorted on the server. |  | web: app/(client)/accounts/[domain]/[sheet]/page.tsx |
| GET | `/v1/accounts/{domain}/sheets/{sheet}/cell` | `routes/account_workspace.py:559` account_sheet_cell | all 4 roles [read_public] | read | One whole cell of a deep-dive table (the page shows a long cell cut to ``CELL_CAP``): row ``n`` under the same filter and sort as the page, column ``col``. |  | web: components/accounts/SheetView.tsx |
| GET | `/v1/accounts/{domain}/archive` | `routes/account_workspace.py:720` account_archive | all 4 roles [read_public] | read | The archived copy of one address the research stored for this account (the newest capture): the original address, the kind of link, when it was captured, the text and, for a posting, its structured re |  | web: app/(client)/accounts/[domain]/archive/page.tsx + |
| GET | `/v1/accounts/{domain}/archive/list` | `routes/account_workspace.py:813` account_archive_list | all 4 roles [read_public] | read | The account's archive index: the newest copy of each stored address, newest capture first, one page (``limit`` at most 200). |  | web: app/(client)/accounts/[domain]/archive/page.tsx |
| GET | `/v1/accounts/{domain}/sheets/{sheet}/tabs/{tab}` | `routes/account_workspace.py:919` account_sheet_tab | all 4 roles [read_public] | read | One sheet of the delivered workbook (Summary, Platform Ranking, Platform teams, Counts), as the file prints it: its text lines and tables, formulas computed, links kept. |  | web: app/(client)/accounts/[domain]/[sheet]/page.tsx |
| GET | `/v1/accounts/{domain}/sheets/{sheet}/xlsx` | `routes/account_workspace.py:973` account_sheet_xlsx | all 4 roles [export_files] | export | Every row of the filtered, sorted table as an Excel workbook (an audited export; a table longer than one Excel sheet continues on the next; ``replica.table_export``). |  | web: components/accounts/SheetView.tsx |
| GET | `/v1/accounts/{domain}/sheets/{sheet}/csv` | `routes/account_workspace.py:1045` account_sheet_csv | all 4 roles [export_files] | export | Every row of the filtered, sorted table as CSV, streamed (an audited export). |  | web: components/accounts/SheetView.tsx |
| GET | `/v1/accounts` | `routes/accounts.py:68` list_accounts | all 4 roles [read_public] | read | The tenant's accounts by domain, each with its latest run (excluded companies hidden). | account_runs, accounts | web: app/(client)/accounts/page.tsx + |
| POST | `/v1/accounts` | `routes/accounts.py:141` add_account | admin, analyst, seller [add_account] | run | Add an account to the tenant's list. |  | web: app/(client)/accounts/page.tsx + |
| DELETE | `/v1/accounts/{domain}` | `routes/accounts.py:183` delete_account | admin, analyst, seller [add_account] | run | Remove an account from the workspace. |  | web: components/accounts/DeleteAccount.tsx |
| GET | `/v1/accounts/{domain}` | `routes/accounts.py:225` read_account | all 4 roles [read_public] | read | One account of the tenant with its newest runs (status labels as the client sees them). | account_runs, accounts | web: components/accounts/DeleteAccount.tsx |
| GET | `/v1/accounts/{domain}/tables/{table}` | `routes/accounts.py:343` read_table | all 4 roles [read_public] | read | One page of a client table. |  | no web caller found (API only) |
| GET | `/v1/library/available` | `routes/library.py:66` library_available | all 4 roles [read_public] | read | The ready-made research this client may add, and the caller's remaining places. |  | web: app/(client)/accounts/add/page.tsx |
| POST | `/v1/accounts/from-library` | `routes/library.py:88` from_library | admin, analyst, seller [start_run] | run | Copy the chosen library accounts into the workspace (no credits; counts toward the cap). |  | web: components/accounts/AddAccount.tsx |
| GET | `/v1/accounts/{domain}/parity/versions` | `routes/parity.py:186` parity_versions | all 4 roles [read_public] | read | Every version of the account's bundle, newest first, built or failed. |  | no web caller found (API only) |
| GET | `/v1/accounts/{domain}/parity/bundle` | `routes/parity.py:200` parity_bundle | all 4 roles [read_public] | read | The bundle document of a built version (the newest when no version is given). |  | no web caller found (API only) |
| GET | `/v1/accounts/{domain}/parity/manifest` | `routes/parity.py:216` parity_manifest | all 4 roles [read_public] | read | The manifest of a built version: its files and the recalculated Summary values. |  | no web caller found (API only) |
| GET | `/v1/accounts/{domain}/parity/screen` | `routes/parity.py:233` parity_screen | all 4 roles [read_public] | read | What the account workspace draws from a built version in one read. |  | web: app/(client)/accounts/[domain]/page.tsx |
| GET | `/v1/accounts/{domain}/parity/tables/{table}` | `routes/parity.py:282` parity_table | all 4 roles [read_public] | read | A page of one table's rows, in the file's order and with the file's cell text. |  | web: components/accounts/ParityTable.tsx |
| GET | `/v1/accounts/{domain}/parity/files/{name}` | `routes/parity.py:314` parity_file | all 4 roles [export_files] | export | One stored file of a built version as an attachment, with the right MIME type; the internal-only file only for an admin (anyone else sees 404). |  | web: components/accounts/ParityWorkspace.tsx |
| POST | `/v1/accounts/{domain}/parity/edits` | `routes/parity.py:358` parity_edit | admin, analyst [edit_shared_views] | run | Apply analyst corrections (priority, lane, note by person) to a built version: the corrections are recorded as overrides, every number is recomputed and the files are rendered again as the account's n |  | no web caller found (API only) |
| GET | `/v1/views` | `routes/views.py:75` list_views | all 4 roles [read_public] | read | The caller's and the tenant's shared views. |  | no web caller found (API only) |
| POST | `/v1/views` | `routes/views.py:87` create_view | admin, analyst, seller [save_own_view] | admin | Save a view. |  | no web caller found (API only) |
| PUT | `/v1/views/{view_id}` | `routes/views.py:117` edit_view | admin, analyst, seller [save_own_view] | admin | Edit a view. |  | no web caller found (API only) |
| POST | `/v1/views/{view_id}/archive` | `routes/views.py:150` archive_view | admin, analyst, seller [save_own_view] | admin | Archive a view (it is never deleted). |  | no web caller found (API only) |

### Downloads and exports

| Method | Path | Handler | Who may call (catalogue) | Audit | What it does | Tables / notes | Called by |
|---|---|---|---|---|---|---|---|
| POST | `/v1/accounts/{domain}/download-everything` | `routes/downloads.py:62` start_everything | all 4 roles [export_files] | export | Queue the archive build (see the module docstring). |  | web: components/accounts/DownloadEverything.tsx |
| GET | `/v1/downloads/{job_id}` | `routes/downloads.py:93` download_status | all 4 roles [read_public] | read | The archive's status and, when ready, its parts with fresh short-lived links. |  | web: components/accounts/DownloadEverything.tsx |
| GET | `/v1/downloads/{job_id}/parts/{n}` | `routes/downloads.py:120` download_part | all 4 roles [export_files] | export | One zip part, whole or a byte range (see the module docstring). |  | web: components/accounts/DownloadEverything.tsx (link from status) |
| POST | `/v1/exports` | `routes/exports.py:94` create_export | all 4 roles [export_files] | export | Build, check and record one package. |  | web: components/ws/RunSteps.tsx |
| GET | `/v1/exports` | `routes/exports.py:242` list_exports | all 4 roles [export_files] | read | An account's packages, newest first (files and parity verdicts; never cell content). | accounts, export_packages | web: components/ws/RunSteps.tsx |
| GET | `/v1/exports/{package_id}` | `routes/exports.py:285` read_export | all 4 roles [export_files] | read | One package of the caller's tenant. |  | no web caller found (API only) |
| GET | `/v1/exports/{package_id}/files/{index}` | `routes/exports.py:304` download | all 4 roles [export_files] | export | One file of a delivered package (the object must be the session tenant's). |  | web: components/ws/RunSteps.tsx |

### Signals, alerts, subscriptions, feedback

| Method | Path | Handler | Who may call (catalogue) | Audit | What it does | Tables / notes | Called by |
|---|---|---|---|---|---|---|---|
| POST | `/v1/feedback` | `routes/feedback.py:55` give | all 4 roles [give_feedback] | none | Record one verdict. |  | no web caller found (API only) |
| GET | `/v1/feedback` | `routes/feedback.py:85` review | admin only [read_usage] | read | Current verdicts, optionally for one account. |  | no web caller found (API only) |
| POST | `/v1/usefulness` | `routes/feedback.py:109` useful | all 4 roles [give_feedback] | none | Record one event (a repeat the same day is accepted and not counted again). |  | no web caller found (API only) |
| GET | `/v1/usefulness/metric` | `routes/feedback.py:131` tenant_metric | admin only [read_usage] | read | The tenant's usefulness metric with its base. |  | no web caller found (API only) |
| GET | `/v1/signals` | `routes/signals.py:47` signals | all 4 roles [read_public] | read | One page of signal events. |  | no web caller found (API only) |
| GET | `/v1/alerts` | `routes/signals.py:61` inbox | all 4 roles [read_public] | read | The caller's alerts (newest first). | accounts, alert_outbox, alerts | no web caller found (API only) |
| POST | `/v1/alerts/{alert_id}/feedback` | `routes/signals.py:102` rate | all 4 roles [give_feedback] | none | Rate one of the caller's alerts. | alert_outbox | no web caller found (API only) |
| GET | `/v1/alerts/usefulness` | `routes/signals.py:130` usefulness | admin only [read_usage] | read | The usefulness metric with its base and the target (never as a result). |  | no web caller found (API only) |
| GET | `/v1/subscriptions` | `routes/signals.py:158` my_settings | all 4 roles [read_public] | read | The caller's settings (the digest-only defaults when none were saved). |  | no web caller found (API only) |
| PUT | `/v1/subscriptions` | `routes/signals.py:165` save_settings | all 4 roles [give_feedback] | admin | Save the caller's settings (rules must exist; accounts must be the tenant's). | accounts | no web caller found (API only) |
| POST | `/v1/subscriptions/subscribe-all` | `routes/signals.py:215` subscribe_all | all 4 roles [give_feedback] | admin | Subscribe the caller to every immediate rule: explicit and audited. |  | no web caller found (API only) |
| GET | `/v1/monitoring/budget` | `routes/signals.py:246` budget | admin only [read_usage] | read | The monitoring allowance used this month, in percent only. | plan_assignments | no web caller found (API only) |
| GET | `/v1/accounts/{domain}/history` | `routes/signals.py:260` account_history | all 4 roles [read_public] | read | An account's expired signals (newest first). | accounts | no web caller found (API only) |

### Team, settings, audit, usage, support, limits, notifications

| Method | Path | Handler | Who may call (catalogue) | Audit | What it does | Tables / notes | Called by |
|---|---|---|---|---|---|---|---|
| GET | `/v1/audit` | `routes/audit.py:39` list_audit | admin only [read_audit] | read | One page of the tenant's audit events. | audit_events | no web caller found (API only) |
| GET | `/v1/audit/export` | `routes/audit.py:83` export_audit | admin only; fresh MFA step-up [export_audit] | export | The client-safe CSV of the audit trail (categories, never providers, credits or cost). |  | no web caller found (API only) |
| GET | `/v1/credits` | `routes/credits.py:60` credits_now | all 4 roles [read_public] | read | The caller's client pool for this month. |  | no web caller found (API only) |
| GET | `/v1/notifications` | `routes/credits.py:236` notifications | all 4 roles [read_public] | read | The caller's latest notifications, unread first. |  | web: app/(client)/layout.tsx |
| POST | `/v1/notifications/{notification_id}/read` | `routes/credits.py:263` notification_read | all 4 roles [read_public] | none | Mark one of the caller's notifications read. |  | web: components/ws/WorkspaceShell.tsx |
| GET | `/v1/me/limits` | `routes/limits.py:55` my_limits | all 4 roles [read_public] | read | The caller's limits and use this period. |  | web: components/Limits.tsx |
| POST | `/v1/limits/requests` | `routes/limits.py:62` request_more | all 4 roles [give_feedback] | admin | File a request for more researches or chats. |  | web: components/Limits.tsx |
| GET | `/v1/settings/people-consent` | `routes/settings.py:77` read_people_consent | all 4 roles [read_public] | read | The consent text, its version, and whether this workspace has granted it. |  | web: app/(client)/team/page.tsx + |
| POST | `/v1/settings/people-consent` | `routes/settings.py:85` set_people_consent | admin only; fresh MFA step-up [manage_settings] | admin | Grant or withdraw the consent (insert-only rows; a withdrawal is a newer row). |  | web: app/(client)/team/page.tsx + |
| GET | `/v1/settings` | `routes/settings.py:133` read_settings | all 4 roles [read_public] | read | The tenant's current settings (version 0 is the platform default, never stored). |  | no web caller found (API only) |
| PUT | `/v1/settings` | `routes/settings.py:149` save_settings | admin only; fresh MFA step-up [manage_settings] | admin | Save a new version. |  | no web caller found (API only) |
| GET | `/v1/branding` | `routes/settings.py:195` read_branding | all 4 roles [read_public] | read | The tokens views and documents use (the attribution is fixed). |  | no web caller found (API only) |
| POST | `/v1/settings/branding/logo` | `routes/settings.py:211` upload_logo | admin only; fresh MFA step-up [manage_settings] | admin | Upload a logo: quarantined and scanned BEFORE any decoder sees it, then re-encoded. |  | no web caller found (API only) |
| PUT | `/v1/settings/branding` | `routes/settings.py:258` save_branding | admin only; fresh MFA step-up [manage_settings] | admin | Save branding as a new settings version (validated tokens only). |  | no web caller found (API only) |
| GET | `/v1/support/grants` | `routes/support.py:50` grants | admin only [grant_support] | read | The tenant's grants. |  | web: app/(client)/support-access/page.tsx + |
| POST | `/v1/support/grants` | `routes/support.py:57` create | admin only; fresh MFA step-up [grant_support] | admin | Grant one operator time-boxed access. |  | web: app/(client)/support-access/page.tsx + |
| POST | `/v1/support/grants/{grant_id}/revoke` | `routes/support.py:86` revoke | admin only [grant_support] | admin | End a grant now. |  | web: components/accounts/SupportAccess.tsx |
| GET | `/v1/support/events` | `routes/support.py:108` events | admin only [read_audit] | read | Every support action in this tenant. | support_session_events | web: app/(client)/support-access/page.tsx |
| GET | `/v1/usage` | `routes/usage.py:38` usage | admin only [read_usage] | read | The period's usage in units. |  | no web caller found (API only) |
| GET | `/v1/usage/export` | `routes/usage.py:49` usage_export | admin only [read_usage] | export | The period summary as CSV (billing-ready, recomputable from the events). |  | no web caller found (API only) |
| GET | `/v1/users` | `routes/users.py:75` list_users | admin only [manage_users] | read | Every member of the caller's tenant. |  | no web caller found (API only) |
| POST | `/v1/users` | `routes/users.py:83` add_user | admin only; fresh MFA step-up [manage_users] | admin | Add a member. |  | no web caller found (API only) |
| PATCH | `/v1/users` | `routes/users.py:117` change_user | admin only; fresh MFA step-up [manage_users] | admin | Change a member's role or status under the role rules. |  | no web caller found (API only) |
| GET | `/v1/users/team` | `routes/users.py:162` team | admin only [manage_users] | read | The members with email and name; ``pending`` while an invited person has not accepted. |  | web: app/(client)/team/page.tsx |
| POST | `/v1/users/invite` | `routes/users.py:168` invite | admin only; fresh MFA step-up [manage_users] | admin | Invite a teammate (see account_intelligence.api.team_invites for when a link is made). |  | web: components/accounts/Teammates.tsx |

### Operator plane (/operator/v1/*)

| Method | Path | Handler | Who may call (catalogue) | Audit | What it does | Tables / notes | Called by |
|---|---|---|---|---|---|---|---|
| GET | `/operator/v1/console/overview` | `routes/admin_console.py:160` overview | staff: operator only; no tenant data | read | A1: the last 30 days across clients (or the weekly chart for one client). |  | staff console web (app/operator/(console)/page.tsx) |
| GET | `/operator/v1/console/clients` | `routes/admin_console.py:208` clients | staff: operator only; no tenant data | read | A2: every client. |  | staff console web (app/operator/(console)/clients/page.tsx) |
| POST | `/operator/v1/console/clients` | `routes/admin_console.py:286` add_client | staff: operator only; no tenant data | admin | A2 Add client: the client, its settings (invited) and its first user; the invite link is emailed when a mailer is set up and always returned to copy. |  | staff console web (app/operator/(console)/clients/page.tsx) |
| POST | `/operator/v1/console/clients/{tenant_id}/invite` | `routes/admin_console.py:345` renew_invite | staff: operator only; operator names tenant in path | admin | A3: a new invite link for the invited user who has not accepted yet (the old one stops working). | tenants | staff console web (app/operator/(console)/clients/[tenantId]/page.tsx) |
| POST | `/operator/v1/console/clients/{tenant_id}/users` | `routes/admin_console.py:378` invite_client_user | staff: operator only; operator names tenant in path | admin | A3 Invite user: add one more person to a client (the rules: api.team_invites; an existing account is added without a link). |  | staff console web (app/operator/(console)/clients/[tenantId]/page.tsx) |
| GET | `/operator/v1/console/clients/{tenant_id}` | `routes/admin_console.py:449` client_detail | staff: operator only; operator names tenant in path | read | A3: one client's limits, features and library publications. |  | staff console web (app/operator/(console)/clients/[tenantId]/page.tsx) |
| PUT | `/operator/v1/console/clients/{tenant_id}` | `routes/admin_console.py:478` change_client | staff: operator only; operator names tenant in path | admin | A3 Save changes: limits, features, and which passed library accounts it has. |  | staff console web (app/operator/(console)/clients/[tenantId]/page.tsx) |
| GET | `/operator/v1/console/users` | `routes/admin_console.py:534` users | staff: operator only; no tenant data | read | A4: every user's counts and access, across clients. | tenants | staff console web (app/operator/(console)/users/page.tsx) |
| PUT | `/operator/v1/console/users/{tenant_id}/limits` | `routes/admin_console.py:599` change_limits | staff: operator only; operator names tenant in path | admin | A4 Change limits: one user's researches and chats. |  | staff console web (app/operator/(console)/users/page.tsx) |
| GET | `/operator/v1/console/requests` | `routes/admin_console.py:626` limit_requests | staff: operator only; no tenant data | read | Overview and Users: every client's pending requests for more, then the latest decided. | limit_requests, tenants, users | staff console web (app/operator/(console)/RequestsList.tsx) |
| POST | `/operator/v1/console/requests/{tenant_id}/{request_id}/approve` | `routes/admin_console.py:711` approve_request | staff: operator only; operator names tenant in path | admin | Approve a request with the new limits; the user is told in the app. |  | staff console web (app/operator/(console)/RequestsList.tsx) |
| POST | `/operator/v1/console/requests/{tenant_id}/{request_id}/decline` | `routes/admin_console.py:723` decline_request | staff: operator only; operator names tenant in path | admin | Decline a request; the user is told in the app. |  | staff console web (app/operator/(console)/RequestsList.tsx) |
| POST | `/operator/v1/console/users/{tenant_id}/terminate` | `routes/admin_console.py:731` terminate | staff: operator only; operator names tenant in path | admin | A4 Terminate: end the user's access now (history kept, signed out everywhere). |  | staff console web (app/operator/(console)/users/page.tsx) |
| POST | `/operator/v1/console/users/{tenant_id}/re-enable` | `routes/admin_console.py:742` re_enable | staff: operator only; operator names tenant in path | admin | A4 Re-enable: restore access ended by an admin or by idle expiry. |  | staff console web (app/operator/(console)/users/page.tsx) |
| GET | `/operator/v1/console/runs` | `routes/admin_console.py:753` runs | staff: operator only; no tenant data | read | A6: credits by purpose this month, the isolation check and the newest jobs. | tenants | staff console web (app/operator/(console)/runs/page.tsx) |
| GET | `/operator/v1/console/defaults` | `routes/admin_console.py:823` defaults | staff: operator only; no tenant data | read | A7: the platform defaults. |  | staff console web (app/operator/(console)/defaults/page.tsx) |
| PUT | `/operator/v1/console/defaults` | `routes/admin_console.py:852` change_defaults | staff: operator only; no tenant data | admin | A7 Save defaults. |  | staff console web (app/operator/(console)/defaults/page.tsx) |
| GET | `/operator/v1/console/runs/{tenant_id}/{run_id}` | `routes/admin_console.py:884` run_detail | staff: operator only; operator names tenant in path | read | A6 detail: one run of one client, with the same token card the client sees. |  | staff console web (app/operator/(console)/runs/[tenantId]/[runId]/page.tsx) |
| GET | `/operator/v1/pilot/aggregate` | `routes/feedback.py:138` aggregate | staff: operator only; no tenant data | read | Per-tenant counts (no content). |  | staff API only (no console page found) |
| GET | `/operator/v1/library` | `routes/library.py:119` library_list | staff: operator only; no tenant data | read | Every current library account (platform rows; counts only from the clients). |  | staff console web (app/operator/(console)/library/page.tsx) |
| POST | `/operator/v1/library/uploads` | `routes/library.py:126` library_upload | staff: operator only; no tenant data | admin | Inspect and store one library package (the request body is the file). |  | staff console web (app/operator/(console)/library/page.tsx) |
| POST | `/operator/v1/library/{library_account_id}/publish` | `routes/library.py:164` library_publish | staff: operator only; no tenant data | admin | Publish a passed library account to each chosen client. |  | staff console web (app/operator/(console)/library/page.tsx) |
| POST | `/operator/v1/library/{library_account_id}/unpublish` | `routes/library.py:188` library_unpublish | staff: operator only; no tenant data | admin | Stop offering a library account to each chosen client (accounts already added stay). |  | staff console web (app/operator/(console)/library/page.tsx) |
| GET | `/operator/v1/session` | `routes/operator_admin.py:70` operator_session | staff: operator or support; no tenant data | read | Return only the verified staff principal, role and MFA freshness. |  | staff console web (app/api/operator/callback/route.ts) |
| GET | `/operator/v1/tenants` | `routes/operator_admin.py:117` overview | staff: operator or support; no tenant data | read | Every tenant's metadata (no names, no content). |  | staff API only (no console page found) |
| POST | `/operator/v1/tenants` | `routes/operator_admin.py:131` provision | staff: operator only; no tenant data | admin | Provision a tenant. |  | staff API only (no console page found) |
| POST | `/operator/v1/tenants/{tenant_id}/suspend` | `routes/operator_admin.py:173` suspend | staff: operator only; operator names tenant in path | admin | Suspend a tenant (sessions and runs stop within the membership-cache bound). |  | staff console web (app/operator/(console)/clients/[tenantId]/page.tsx) |
| POST | `/operator/v1/tenants/{tenant_id}/reactivate` | `routes/operator_admin.py:181` reactivate | staff: operator only; operator names tenant in path | admin | Lift a suspension. |  | staff console web (app/operator/(console)/clients/[tenantId]/page.tsx) |
| POST | `/operator/v1/tenants/{tenant_id}/quotas` | `routes/operator_admin.py:198` set_quota | staff: operator only; operator names tenant in path | admin | A new quota version for one tenant. |  | staff API only (no console page found) |
| POST | `/operator/v1/tenants/{tenant_id}/plan` | `routes/operator_admin.py:223` set_plan | staff: operator only; operator names tenant in path | admin | A new plan assignment version for one tenant. |  | staff API only (no console page found) |
| POST | `/operator/v1/tenants/{tenant_id}/deletion` | `routes/operator_admin.py:244` request_deletion | staff: operator only; operator names tenant in path | admin | Open a deletion request (dry run and a second operator come next). |  | staff API only (no console page found) |
| POST | `/operator/v1/tenants/{tenant_id}/deletion/{request_id}/dry-run` | `routes/operator_admin.py:294` deletion_dry_run | staff: operator only; operator names tenant in path | admin | Count exactly what would be removed and what is blocked. |  | staff API only (no console page found) |
| POST | `/operator/v1/tenants/{tenant_id}/deletion/{request_id}/approve` | `routes/operator_admin.py:302` deletion_approve | staff: operator only; operator names tenant in path | admin | The second person's approval. |  | staff API only (no console page found) |
| POST | `/operator/v1/tenants/{tenant_id}/deletion/{request_id}/execute` | `routes/operator_admin.py:310` deletion_execute | staff: operator only; operator names tenant in path | admin | One bounded, resumable pass. |  | staff API only (no console page found) |
| GET | `/operator/v1/flags` | `routes/operator_admin.py:325` list_flags | staff: operator only; no tenant data | read | Every flag's newest rule. | feature_flags | staff API only (no console page found) |
| POST | `/operator/v1/flags` | `routes/operator_admin.py:344` set_flag | staff: operator only; no tenant data | admin | A new flag version (with its change event). |  | staff API only (no console page found) |
| GET | `/operator/v1/keys` | `routes/operator_admin.py:372` list_keys | staff: operator only; no tenant data | read | Key versions WITHOUT their references (write-only). |  | staff API only (no console page found) |
| POST | `/operator/v1/keys` | `routes/operator_admin.py:381` add_key | staff: operator only; no tenant data | admin | A new key reference version (takes effect for runs created after it). |  | staff API only (no console page found) |
| GET | `/operator/v1/view/accounts` | `routes/operator_view.py:32` accounts | staff: operator or support; needs X-Support-Grant (tenant admin granted) | read | The tenant's accounts and latest run states, as the tenant sees them. | account_runs, accounts | staff API only (no console page found) |
| GET | `/operator/v1/view/settings` | `routes/operator_view.py:57` tenant_settings | staff: operator or support; needs X-Support-Grant (tenant admin granted) | read | The tenant's current settings, as the tenant sees them. |  | staff API only (no console page found) |
| GET | `/operator/v1/view/workspace` | `routes/operator_view.py:75` workspace | staff: operator or support; needs X-Support-Grant (tenant admin granted) | read | View as a client: the workspace as the client sees its accounts (names and counts). | account_runs, accounts, memberships, people, signal_events | staff console web (app/operator/(console)/view/page.tsx) |
| GET | `/operator/v1/runs/{run_id}/timeline` | `routes/ops.py:32` run_timeline | staff: operator or support; needs X-Support-Grant (tenant admin granted) | read | The run's timeline from durable events. |  | staff API only (no console page found) |
| GET | `/operator/v1/ops/cost/{run_id}` | `routes/ops.py:64` run_cost | staff: operator or support; needs X-Support-Grant (tenant admin granted) | read | The run's cost rollup (refreshed) and its reconciliation with the ledger. |  | staff API only (no console page found) |
| GET | `/operator/v1/ops/failures` | `routes/ops.py:87` failures | staff: operator or support; needs X-Support-Grant (tenant admin granted) | read | Failed and partial runs by status and reason (counts only). | account_runs | staff API only (no console page found) |
| GET | `/operator/v1/ops/stuck` | `routes/ops.py:104` stuck | staff: operator or support; needs X-Support-Grant (tenant admin granted) | read | The latest stuck-run observations (identifiers and reasons only). | stuck_run_observations | staff API only (no console page found) |
| GET | `/operator/v1/runs/{run_id}/spend/live` | `routes/spend_v2.py:210` operator_live | staff: operator or support; needs X-Support-Grant (tenant admin granted) | read | The operator view of live spend (amounts by provider code, the labelled projection). |  | staff API only (no console page found) |
| POST | `/operator/v1/runs/{run_id}/prepulled/intents` | `routes/spend_v2.py:235` intent_target | staff: operator or support; needs X-Support-Grant (tenant admin granted) | admin | The exact file spec and a signed quarantine upload target. |  | staff API only (no console page found) |
| POST | `/operator/v1/runs/{run_id}/prepulled/{intent_id}/complete` | `routes/spend_v2.py:269` complete_upload | staff: operator or support; needs X-Support-Grant (tenant admin granted) | admin | Scan, validate and register the uploaded file; a typed verdict (never file content). |  | staff API only (no console page found) |
| GET | `/operator/v1/runs/{run_id}/prepulled/{intent_id}` | `routes/spend_v2.py:303` intent_status | staff: operator or support; needs X-Support-Grant (tenant admin granted) | read | The intent's recorded events (codes and hashes only). |  | staff API only (no console page found) |

### Worker tasks and sweeps (/tasks/*)

| Method | Path | Handler | Who may call (catalogue) | Audit | What it does | Tables / notes | Called by |
|---|---|---|---|---|---|---|---|
| POST | `/tasks/run-slice` | `routes/tasks.py:113` run_slice | Google OIDC token of the tasks service account (Cloud Tasks / Scheduler) | - | Run one slice of one run for an OIDC-verified Cloud Tasks call. |  | Cloud Tasks (queue) |
| POST | `/tasks/document-process` | `routes/tasks.py:160` document_process | Google OIDC token of the tasks service account (Cloud Tasks / Scheduler) | - | Scan, parse and index one uploaded document (``documents.service``). |  | Cloud Tasks (documents queue) |
| POST | `/tasks/approval-timeout` | `routes/tasks.py:186` approval_timeout | Google OIDC token of the tasks service account (Cloud Tasks / Scheduler) | - | Expire a run's wait past its deadline: a first-cap approval cancels and refunds (known defect 8); a cap pause (D-45) records the timeout and resumes the run, which then ends BUDGET_EXCEEDED with parti | account_runs | Cloud Tasks (scheduled at deadline) |
| POST | `/tasks/research-job` | `routes/tasks.py:218` research_job | Google OIDC token of the tasks service account (Cloud Tasks / Scheduler) | - | Run one confirmed research job (``replica.jobs.run_job``). |  | Cloud Tasks (jobs queue) |
| POST | `/tasks/download-everything` | `routes/tasks.py:232` download_everything | Google OIDC token of the tasks service account (Cloud Tasks / Scheduler) | - | Build one Download everything archive (``replica.downloads.build``) as its owner. |  | Cloud Tasks (jobs queue) |
| POST | `/tasks/schedules-tick` | `routes/tasks.py:258` schedules_tick | Google OIDC token of the tasks service account (Cloud Tasks / Scheduler) | - | Start due scheduled refreshes from each client's pool (Cloud Scheduler, every 15 minutes). |  | Scheduler */15 (UTC) |
| POST | `/tasks/maildrive-sync` | `routes/tasks.py:273` maildrive_sync | Google OIDC token of the tasks service account (Cloud Tasks / Scheduler) | - | Sync the per-user Mail and drive connections not synced in the last hour (Cloud Scheduler; identifiers from ``maildrive_connections_due``, then each tenant's own transaction). | maildrive_connections_due | Scheduler hourly :05 (UTC) |
| POST | `/tasks/access-expiry` | `routes/tasks.py:352` access_expiry | Google OIDC token of the tasks service account (Cloud Tasks / Scheduler) | - | Warn idle users three days before and end the access of those idle for their client's idle days (Cloud Scheduler, daily). |  | Scheduler daily 03:30 (UTC) |
| POST | `/tasks/sweep` | `routes/tasks.py:391` sweep | Google OIDC token of the tasks service account (Cloud Tasks / Scheduler) | - | One sweeper pass on a schedule (Cloud Scheduler, every 5 minutes): documents left mid-way, orphaned and stalled runs. |  | Scheduler */5 (UTC) |
| POST | `/tasks/audit-verify` | `routes/tasks.py:406` audit_verify | Google OIDC token of the tasks service account (Cloud Tasks / Scheduler) | - | The nightly audit chain check (Cloud Scheduler, 04:15): each client's tamper-evident audit chain is re-hashed in bounded batches; a break is logged (and alerts) with its position. | operator_tenant_overview | Scheduler 04:15 (UTC) |
| POST | `/tasks/notify-relay` | `routes/tasks.py:439` notify_relay | Google OIDC token of the tasks service account (Cloud Tasks / Scheduler) | - | Deliver waiting notifications (Cloud Scheduler, every 5 minutes): each client's outbox is relayed by email, operator messages to the staff operator list and client messages to the client's admins. | memberships, operator_tenant_overview, users | Scheduler */5 (UTC) |
| POST | `/tasks/metrics` | `routes/tasks.py:503` metrics_tick | Google OIDC token of the tasks service account (Cloud Tasks / Scheduler) | - | The cluster-wide metrics (Cloud Scheduler, every 2 minutes): the heartbeat the telemetry-down alert watches, the stuck-run gauge by state and age (counted only, never recorded as observations), and th | operator_tenant_overview | Scheduler */2 (UTC) |
| POST | `/tasks/isolation-check` | `routes/tasks.py:560` isolation_check | Google OIDC token of the tasks service account (Cloud Tasks / Scheduler) | - | The nightly isolation check (Cloud Scheduler, 02:00): structure, every tenant table and the read routes, from a dedicated probe client; the pass is recorded either way. |  | Scheduler 02:00 (UTC) |

## 12. Scheduler and queue map (what calls `/tasks/*`)

Cloud Tasks (created by `platform/gcp/adapters.CloudTasksScheduler`, one named task per item so duplicates are harmless): `run-<run>-slice-<n>` to `/tasks/run-slice`; `doc-<doc>-<nonce>` to `/tasks/document-process` (documents queue); `job-<job>` to `/tasks/research-job` and `download-<job>` to `/tasks/download-everything` (jobs queue); `run-<run>-approval-timeout` to `/tasks/approval-timeout` at the deadline. All carry an OIDC token for the worker audience.
Cloud Scheduler (`infra/stack/main.tf` `http_ticks`, UTC): `schedules-tick` every 15 min; `isolation-check` 02:00; `access-expiry` 03:30; `maildrive-sync` hourly at :05; `sweep` every 5 min; `audit-verify` 04:15; `notify-relay` every 5 min; `metrics` every 2 min; plus a Cloud Run job `spend-invariants` 03:17.
In dev/test, `platform/hermetic.py` posts to the same `/tasks/*` URLs in-process.

## 13. What the key routes do inside (read from the handlers)

* **Start research (the product's main path).** `GET /v1/research/estimate` then `POST /v1/research` (body: `request_id`, `company_name`, `domain`, optional models/efforts/goal). In one tenant transaction it: ensures saved tenant settings, refuses excluded companies, resolves the user's models, checks the estimate the user saw (409 `estimate_changed`), adds the account (account cap), takes one of the user's researches (`replica.limits`), reserves credits (409 `credits_short`/`research_in_progress`), writes an audit `start_new_research`. After commit it calls `replica.jobs.run_or_enqueue` which enqueues `/tasks/research-job` on Cloud Tasks. The worker then drives slices via `/tasks/run-slice` (`workflow.runner.execute_slice`, LangGraph checkpoints in Postgres). Stop = `POST /v1/research/{job_id}/stop`; Resume = `POST /v1/research/resume` (reuses finished pulls/replies at no cost).
* **Legacy/file run path.** `POST /v1/runs` (needs `Idempotency-Key`; admin, seller, analyst via `require_role`) creates both rows + credit reservation + tracker in one transaction and enqueues slice 0; `input_mode: file` goes to `file_runs.create_file_run` (uploaded documents only, optional public enrichment as a linked company run). The web still calls this for file mode (`components/accounts/NewResearch.tsx`).
* **Uploads.** `POST /v1/documents/uploads` declares a file and returns a resumable session straight to object storage (or, only where the store has none, a one-time `PUT /v1/documents/{id}/content`); bytes land in quarantine; `POST .../complete` enqueues `/tasks/document-process` (malware scan with clamd, parse, index). `POST /v1/documents/confirm-company` binds documents to a confirmed account; the company is never guessed (409 `company_confirmation_required`).
* **Ask.** `POST /v1/chat/ask` streams SSE (`status`, `text`, `thread`, `answer`, `done`/`error`). Reads the workspace (research facts, tracker, deliverables, the user's uploaded files and own mail/drive), a tabular route (`describe_table`/`query_table` tools, optional sandboxed `run_python` behind an admin flag), conversation history and (per the HEAD commit message and the imports `replica.web_tools`, `replica.archive_tools`) the model's own knowledge and web lookups, with web lookups reported OFF in project notes (unverified in code); the whole answer is buffered, citation-checked and scrubbed before any text is sent; repeated `request_id` replays the stored answer. Models via `ProviderByModel` (Anthropic, Moonshot/Kimi, xAI/Grok). Tables: `chat_threads`, `chat_turns`, `accounts`.
* **Downloads.** `POST /v1/accounts/{domain}/download-everything` queues `/tasks/download-everything`; `GET /v1/downloads/{job_id}` returns zip parts with fresh short-lived tokens; `GET /v1/downloads/{job_id}/parts/{n}?token=` supports byte ranges. Per-table `xlsx`/`csv` exports and deliverable downloads are audited `export` events.
* **Exports.** `POST /v1/exports` builds, parity-checks and scans a package, recording `delivered` or `blocked` with its audit row in one transaction; files are served only for delivered packages with the session (no token in the URL).
* **Parity bundle** (`/v1/accounts/{domain}/parity/*`): versioned built output; the internal-only file and prose are served to tenant admins only; a version that failed the gate is never served; `POST .../parity/edits` applies analyst corrections and renders the next version.
* **Operator console** (`/operator/v1/console/*`): add client (creates tenant + first user + invite link, nothing is emailed), per-user limits, limit-request approval, terminate/re-enable access, defaults, runs/token card; every change is an `operator_audit` row and, for a client, an entry in that client's audit log. Tenant lifecycle (`/operator/v1/tenants/{id}/suspend|reactivate|quotas|plan|deletion...`) is operator-only; deletion is two-person (request, dry-run, approve by a different operator, execute in bounded batches).

## 14. Findings worth the new owner's attention (all from reading code)

1. **Scheduler jobs for routes that do not exist.** `infra/stack/main.tf` defines ticks `retention` (`/tasks/retention`) and `alert-digest` (`/tasks/alert-digest`), both `paused = true`. No such handler exists in `routes/tasks.py` (grep of `src/`, `infra/`, `scripts/` finds the strings only in Terraform). If anyone unpauses them they would get 404 and the alert-digest and retention features have no scheduled runner.
2. **Large unused API surface.** 73 tenant-plane routes have no caller in the web app by static grep (connectors, CRM write-back, usage, audit, settings, branding, subscriptions/alerts/signals, saved views, feedback, provider key, SSO connection admin, most of the parity bundle, the older `tables/{table}` reader, `chat/threads/{id}/turns/stream`, pull-option/collected, cap reject/extend/pauses, etc.). They are reachable by anyone with a session or JWT and the right role, are covered by the catalogue and by tests, but are not exercised by the UI: treat as dormant features, a maintenance cost and attack surface. `web/app/operator/(console)/spend|cost|tenants|support` pages were replaced by redirects, so the operator `spend_v2`, `ops` and most `operator_admin` routes are likewise staff-API only (23 of 49 operator routes).
3. **SAML sign-in through the BFF is probably blocked (unverified).** `POST /v1/sso/saml/acs` is a cross-site form POST from the IdP; the web proxy rejects any non-GET whose `Origin` does not equal the site host (`web/app/v1/[[...path]]/route.ts`), and no SAML exception exists in `web/` (grep). OIDC (a GET callback) is not affected. Needs a real SAML test to confirm.
4. **Admins cannot use password login** (`admin_requires_mfa` true and no TOTP verifier). By design for now (invited users become `analyst`), but a client's first admin must come via SSO with MFA.
5. **Custom error text is dropped.** `HTTPException` details other than `invalid token|forbidden|not found` are replaced by the status title in the JSON body (`api/problem.py`), including the login refusal text and several `HTTPException(409, "...")` messages in routes; only `APIProblem` carries explanatory text.
6. **Tasks are outside the route catalogue.** `/tasks/*` (13 routes) are protected only by the OIDC check in `_authorise`; they are present on the `api` service too (internal ingress), not just the worker. In the cloud both services require `TASKS_AUDIENCE`/`TASKS_SERVICE_ACCOUNT` to build the container, so an unauthenticated call gets 403. Likewise the worker serves every `/v1` route (internal ingress only). Neither is a hole as deployed, but isolation here depends on Cloud Run ingress/IAM, not on code.
7. **Per-process state.** The membership cache (60 s) and the read-audit dedupe table (`guard._READ_SEEN`, cap 20 000) are in-process memory; a role change or removal can take up to 60 s on other instances (documented, `forget()` only clears the local process). Dev rate-limit buckets are in-process too.
8. **Handler vs catalogue duplication.** Several handlers (`spend.py`, `spend_v2.py`, `documents.py`, `runs.py`, `internal_view.py`) declare their own `require_role(...)` on top of the catalogue; they agree today or are looser than the catalogue, so the catalogue wins. When changing a role, change both or only the catalogue.
9. **Catalogue label vs behaviour.** A few routes reuse an unrelated role-action for their roles: chat thread delete uses `save_own_view` (viewers cannot delete their own chats), creating a thread uses `start_run` (viewers cannot create chats via that route while Ask itself is open to all roles); `limits/requests`, `subscriptions` and alerts feedback use `give_feedback`.
10. **Docs drift.** `docs/guide/03-code-map.md` says migrations run "0001 to 0085"; the code expects revision `0097` and there are 195 migration files. The guide's "~35 route modules" is right (38 files incl. `__init__` and `file_runs.py`).
11. **Cap routes still live** (`/v1/runs/{id}/cap/*`, `/cap/pauses/*`) although project memory says research caps are switched off (one switch); the code comment in `policy.py` says they are to be removed by "FIX_PROMPT Phase 6". Unverified whether the cap-off switch makes them no-ops.
12. **Staff step-up applies to reads too** (`require_staff`): every console GET demands MFA/re-auth within 600 s, which is why the console re-prompts often.

## 15. Not verified

* Runtime behaviour of any route (nothing was executed; no DB, no network).
* The exact Cloud Armor value of `rate_limit_per_minute` and the Terraform scaling values.
* Whether the 73 "no web caller" routes are used by external API clients, scripts or the e2e suite (e2e/tests were excluded from the grep).
* The semantics of every `replica.*`, `views.*`, `spend.*` service called by the handlers; only their entry points were read.
* Whether `SERVICE_ROLE=api` instances in staging actually ignore `/tasks/*` calls (they would 403 without a valid token) and how the load balancer/IAM blocks the worker's `/v1` routes (infra read, not tested).
* The table lists under "Tables / notes" come from a regex over each handler's own SQL; handlers that call services (most of them) show an empty or partial list.
