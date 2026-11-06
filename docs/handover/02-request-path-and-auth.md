# 02 Request path and auth

Verified against the code on 2026-11-03 (working tree of `feat/final-product`, uncommitted changes included).

See also: [index](README.md) · [01 architecture](01-architecture-and-planes.md) · [03 run lifecycle](03-research-run-lifecycle.md) · [04 model routing](04-model-routing.md) · [05 spend and caps](05-spend-and-caps.md) · [06 data and isolation](06-data-and-isolation.md) · [07 documents](07-documents-and-sandbox.md) · [08 exports and gate](08-exports-and-gate.md) · [09 ops](09-ops.md) · [10 known gaps](10-known-gaps.md)

## The path of one browser request

```
browser --(same origin, cookies)--> web: app/v1/[[...path]]/route.ts  (BFF proxy)
        --(X-Serverless-Authorization: web SA ID token, allow-listed headers, auth cookies)-->
api: v1_router [route_guard -> authorize] -> handler -> tenant_txn (RLS)
```

### 1. The web BFF (`web/app/v1/[[...path]]/route.ts`)

- Every method is the one `handleProxy` (`web/app/v1/[[...path]]/route.ts:131-137`).
- Non-safe methods must be same-origin, else 403 (`web/app/v1/[[...path]]/route.ts:52-56`). The single exception is the SAML ACS (below).
- Only an allow-list of request headers is forwarded (`web/app/v1/[[...path]]/route.ts:13-27`), plus only the auth cookies `__Host-ai_session`, `__Host-ai_preauth`, `__Host-ai_preauth_once` (`web/lib/tenant-cookies.ts:5-14`, used at `web/app/v1/[[...path]]/route.ts:67-68`).
- Bodies are streamed and capped per route; a declared length over the limit is 413 (`web/app/v1/[[...path]]/route.ts:71-84`).
- The proxy adds the web service's Google ID token for Cloud Run IAM; a browser can never supply it because it is not in the forwarded list (`web/app/v1/[[...path]]/route.ts:87-89`).
- Upstream timeout is 504, connection failure 503 (`web/app/v1/[[...path]]/route.ts:120-128`).

### 2. API invoked by web only (ADR-0127)

- Terraform: `public_invoker = ["web"]` and `invokers.api = { web = <web SA> }` (`infra/stack/main.tf:699-704`); api ingress is internal only (`infra/stack/main.tf:595`).
- `web/lib/serverless-auth.ts`: on Cloud Run (`K_SERVICE` set) it fetches an ID token for the API origin from the metadata server, caches it until 5 minutes before expiry, and sets `x-serverless-authorization` (`web/lib/serverless-auth.ts:10-17`, `:41-64`). Off Cloud Run it returns `{}` (`web/lib/serverless-auth.ts:24-26`, `:42`). A metadata failure throws, so the call fails as an upstream error, never unauthenticated (`web/lib/serverless-auth.ts:53-55`).
- `Authorization` is left to the application, which matters for the operator proxy that sends the staff bearer there (`web/app/api/operator/v1/[...path]/route.ts:51`, `:81-83`).
- The api still authenticates every business request itself (section 3).

### 3. API: route catalogue and default deny

- Every `/v1` and `/operator/v1` route has exactly one `RoutePolicy`: roles, principal types, audit class, tenant source (`src/account_intelligence/api/policy.py:1-18`, `:38-49`). `register` refuses duplicates; `policy_for` returns None to deny by default (`src/account_intelligence/api/policy.py:152-163`).
- Routers: `v1_router` has `route_guard` then `authorize` (`src/account_intelligence/api/routes/__init__.py:51`); anonymous sign-in routes get `route_guard` only (`:88-90`); the operator router `route_guard` only, with staff dependencies per route (`:91-98`).
- `route_guard`: 403 when the matched route template has no catalogue entry (`src/account_intelligence/api/guard.py:64-69`).
- `authorize` (`src/account_intelligence/api/guard.py:72-118`): principal type and role must match the entry (`:86-87`); membership re-checked through a ~60 s cache, fail-closed 503 on a backend error (`:88`, `src/account_intelligence/api/auth.py:139-153`); CSRF token required on unsafe cookie-session calls (`src/account_intelligence/api/guard.py:90-99`); step-up (MFA within the window) for actions that need it (`:100-107`); then the request acts as the caller in user scope for RLS (`:110-114`); `read`-class routes write a deduplicated audit read (`:115-116`).
- Build check: `scripts/check_route_catalog.py` fails when a route lacks an entry, an entry lacks a route, a tenant route takes its tenant from anywhere but the session, or a route declares `tenant_id` (`scripts/check_route_catalog.py:1-8`).

### 4. Tenant identity (`identity/sessions`, `api/auth.py`)

- Two credentials on tenant routes: a bearer JWT (checked first) or the `__Host-ai_session` cookie (`src/account_intelligence/api/auth.py:109-116`); the tenant comes only from the verified credential (`src/account_intelligence/api/auth.py:1-12`).
- Server-side sessions: 256-bit random token in an HttpOnly, Secure, SameSite cookie; only its SHA-256 is stored; ends on idle, absolute timeout, revocation or rotation (`src/identity/sessions.py:1-9`).
- Policy (`config/session.yaml:8-17`): idle 1800 s, absolute 28800 s, `step_up_window_s` 600, admin requires MFA, cookie `SameSite=Lax`, CSRF header `X-CSRF-Token`. The file is still `status: proposal` (`config/session.yaml:6`).
- `GET /v1/sso/session` returns the CSRF token for the session (`src/account_intelligence/api/routes/sso.py:198-211`).

### 5. SAML ACS through the BFF (ADR-0110)

- `web/lib/cross-site.ts` allows exactly `POST /v1/sso/saml/acs` (`web/lib/cross-site.ts:10`, `:15-17`), form-encoded only (else 415, `web/app/v1/[[...path]]/route.ts:47-51`), at most 256 KB (`web/lib/cross-site.ts:12`, `web/app/v1/[[...path]]/route.ts:71-73`), with no same-origin check. No other route's Origin rule changed (`web/app/v1/[[...path]]/route.ts:52-56`).
- The proxy forwards the auth cookies; the pre-auth cookie is `SameSite=None` for SAML on purpose (`src/account_intelligence/api/routes/sso.py:9-14`, `:134`).
- API side: `POST /v1/sso/saml/acs` is registered `ANONYMOUS` (`src/account_intelligence/api/routes/sso.py:53`, handler `:177-195`). `complete_saml` claims the RelayState (`src/identity/broker.py:296`), which `_claim_state` refuses when unknown, not bound to this browser's pre-auth cookie, replayed, expired or of another protocol (`src/identity/broker.py:167-200`); each assertion id is inserted once into `saml_assertion_ids`, a duplicate is `assertion_replayed` (`src/identity/broker.py:309-323`).
- SAML parsing uses `signxml`/`lxml` with DTDs and entities off and the signature-wrapping defence (`src/identity/saml.py:1-18`).

### 6. Operator sessions (ADR-0111)

- Sign-in: the web callback verifies the Google ID token through `GET /operator/v1/session` (`web/app/api/operator/callback/route.ts:45-60`), then trades it once via `POST /operator/v1/sessions` (`web/lib/operator-session.ts:14-34`, called at `web/app/api/operator/callback/route.ts:138`).
- The API mints `ops_` + `token_urlsafe(32)` and stores only its SHA-256 in `operator_sessions` (`src/account_intelligence/api/operator_sessions.py:35-36`, `:52-62`). A session token cannot mint another (`src/account_intelligence/api/routes/operator_admin.py:93-94`), so the absolute limit holds.
- Idle 30 min / absolute 8 h reuse the customer session policy (`src/account_intelligence/api/operator_sessions.py:8-16`, `resolve` at `:77-99`).
- Cookie: HttpOnly, `SameSite=strict`, `Secure` when the redirect URI is https (`web/app/api/operator/callback/route.ts:146-150`, `web/lib/operator-oidc.ts:60`).
- `api.operator.staff` accepts either an `ops_` session or a raw staff IdP token (`src/account_intelligence/api/operator.py:212-246`); `require_staff` still enforces the route's operator roles and a fresh step-up (`src/account_intelligence/api/operator.py:249-258`), so the 10-minute MFA window, not the 8 h limit, bounds console use (ADR-0111).
- Sign-out: `DELETE /operator/v1/sessions/current` revokes it (`web/lib/operator-session.ts:37-50`, `src/account_intelligence/api/routes/operator_admin.py:106-119`).

## Client feature flags

- `admin/flags.evaluate`: on when the tenant is listed, its plan is listed, or its stable bucket is under the percentage; **off for a missing rule** (deny by default) (`src/admin/flags.py:24-34`). Rules are insert-only versions; `current` reads the newest (`src/admin/flags.py:49-58`).
- `@behind_flag(flag)` (ADR-0112, `src/account_intelligence/api/flags.py:68-104`) runs after the route's own auth and before its body, and answers the same 404 as an unknown path when the flag is off (`src/account_intelligence/api/flags.py:56-60`). `flag_on` returns False without reading the plan when no rule exists (`src/account_intelligence/api/flags.py:42-53`).
- `GATED_FLAGS` is the one list: `connectors`, `crm`, `monitoring` (`src/account_intelligence/api/flags.py:32-37`); an unknown flag is a `ValueError` at import (`:70-71`). Gated today: 8 routes in `api/routes/connectors.py`, 7 in `api/routes/crm.py`, 9 in `api/routes/signals.py` (counted by grep on `behind_flag(`; e.g. `src/account_intelligence/api/routes/signals.py:47-48`).
- Turning a feature on is an operator flag change (`POST /operator/v1/flags`), no deploy (ADR-0112).
- The same `evaluate` also decides the `abm_agents` flag for which research graph a run uses (`uses_abm` in `src/agents/production/wiring.py`; page 03).

## Worker-plane auth (for completeness)

`/tasks/*` require a Google-signed OIDC token for the worker audience from the scheduler/tasks service account, verified with a 20 s timeout (503 on timeout) (`src/account_intelligence/api/routes/tasks.py:83-110`). In dev/test only, `ALLOW_UNAUTHENTICATED_TASKS=1` opens them (`src/account_intelligence/api/routes/tasks.py:86-91`). Body carries ids only; the tenant is re-checked under RLS (`src/account_intelligence/api/routes/tasks.py:20-24`).

## Code vs ADR

- ADR-0111 says the operator cookie is `Secure`; the code sets `secure: config.secureCookies`, true only when the redirect URI is https (`web/lib/operator-oidc.ts:60`). Correct in the cloud, off on http dev.
- ADR-0127 is "Terraform planned, NOT applied"; the `.tf` on disk already contains it (`infra/stack/main.tf:699-704`). Confirm in the Cloud Run console that the api's invokers list only the web service account.
- `config/session.yaml` still says `status: proposal` and "TODO, needs product owner" (`config/session.yaml:6-7`) although ADR-0111 treats its values as the decided limits.
