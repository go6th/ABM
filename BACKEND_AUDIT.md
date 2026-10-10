# Backend audit — Account Intelligence production web app

**Gate:** Audit with implementation follow-through  
**Date:** 2026-10-09  
**Scope:** Current working tree, including uncommitted changes and auth implementation follow-through recorded in `SYNC.md`. This is a code/configuration review, not a production deployment review. Production infrastructure and external identity, mail, and payment services were not accessed.

## Executive finding

The repository is an established multi-tenant research platform with a Next.js app and substantial policy, lifecycle, and isolation machinery. It is not a clean implementation base for the attached task: there are already broad, uncommitted changes, including a new staff authentication path. Preserve and review those changes as user work.

**Production auth is still incomplete.** The unsafe staff-password login was removed along with its migration and route. Operator sign-in accepts the configured Google Workspace OIDC flow and the backend validates the token and MFA before the BFF stores it in an HttpOnly cookie. Tenant sign-in uses the existing SSO broker and server-side HttpOnly session. First-party local sign-in now uses Argon2id, verified-email membership resolution, same-origin BFF requests, account rate limiting, SSO-member password enrollment, single-use email verification, and the existing server-side session with rotation/revocation. Self-service tenant registration, password reset delivery, production mail, local MFA/recovery, and refresh-token family rotation/reuse detection remain open.

The architecture and two PRD source files are in `docs/sources/`; the user later supplied `Account_Intelligence_Production_Roadmap.pdf` from Downloads. Its technology section recommends Temporal for orchestration, while Architecture v2.0 specifies LangGraph as state owner with Inngest for triggers. The existing repo uses LangGraph plus Cloud Tasks. This conflict is recorded as an unresolved architecture choice; see `DECISIONS.md`. The audit also uses the repo roadmap at `docs/ROADMAP.md`, which is distinct from the supplied production roadmap PDF.

## Repository inventory

| Area | Current implementation |
|---|---|
| Backend | Python 3.11–3.12, FastAPI, Pydantic 2, SQLAlchemy async, PostgreSQL/pgvector; package configuration in `pyproject.toml`. |
| Entrypoints | API `account_intelligence.api.main:app`; worker task HTTP surface under `/tasks`; worker/job/migrator roles selected by `SERVICE_ROLE`; Alembic migrations under `migrations/`. |
| Persistence | PostgreSQL, migrations through 0047 for local credential foundations, tenant DB access via `tenant_txn()` and separate `untenanted_session()` for narrow identity lookups. Runtime settings and environment registry in `src/account_intelligence/config.py`. |
| Research/durability | LangGraph checkpointing, time-sliced runner, Cloud Tasks scheduler, worker task endpoints; no Inngest HTTP route found. Temporal is not the active orchestrator. This differs from the architecture v2.0 narrative (Inngest + LangGraph) and the prompt's requested orchestrator-agnostic UI. |
| Integrations | Provider adapters, Redis rate limiting, Google Cloud Tasks/Storage/KMS/Secret Manager; OAuth/SAML tenant SSO and support-grant operator model exist. |
| Web | Next.js App Router under `web/app`; tenant and operator surfaces, API proxy/BFF, generated OpenAPI types, CSP nonce proxy, CSS design tokens, Vitest/Playwright configuration. Several existing files are uncommitted. |
| Contract | `docs/api/openapi.json`, contract code in `src/account_intelligence/api/contract.py`, generated TS in `web/lib/gen/api.gen.ts`, and contract checks in CI. Added `GET /v1/me` for server-derived capabilities, plan limits, flags, identity and MFA state. No `/contract/openapi.json` path found. |
| Local stack | `compose.yml` has PostgreSQL, fake GCS, Cloud Tasks emulator, Redis, API, worker, fake IdP, and web; it enables unauthenticated worker tasks only for the dev profile. |
| Delivery | CI includes lint/static/contract/isolation/coverage, web typecheck/lint/unit/build, Playwright E2E, container scan, SBOM and signing. Added an optional `deploy/helm/web` chart with Deployment/Argo Rollouts, PDB, HPA, ExternalSecret mount, probes, ingress and NetworkPolicy. |

### Existing API surface

`docs/api/openapi.json` is the detailed method/path and schema catalogue. It includes health/readiness; tenant accounts, alerts, audit, chat, connectors, CRM, exports, feedback, models, monitoring, runs (create/list/detail/progress/events/approval/cancel/spend/cap), settings/branding, local auth login, signals, SSO, subscriptions, support, usage, usefulness, users and saved views; and operator flags/keys/cost/failures/stuck runs/tenants/support views/deletion/plan/quota actions. Worker routes are `/tasks/run-slice` and `/tasks/approval-timeout` and are not included in the public OpenAPI snapshot. Route authorization is default-deny via `ROUTE_POLICIES`; route source files define role, principal, tenant source and audit class.

**Schemas/conventions:** OpenAPI has request and response schemas for versioned API routes; run creation takes a validated `domain`, `depth`, optional mode fields and `Idempotency-Key`, returns 202; run list uses keyset cursor; run detail/progress implement ETag/304; errors are emitted as problem-details with request IDs and validation field paths; rate-limit helpers preserve `Retry-After`. `GET /v1/accounts` uses `next_after` domain pagination rather than a uniform cursor. The contract is checked against code by `tests/api/test_openapi_snapshot.py` and CI `make contract`.

## Architecture v2.0 / product drift (chapters 4, 5, 7, 9, 18)

| Topic | Status | Evidence / assessment |
|---|---|---|
| Tenant RLS and transaction context (4, 18) | **Implemented in core; local auth foundation now follows narrow resolver pattern** | `tenant_txn()` sets transaction-local `app.tenant_id`; migrations 0002 onward use ENABLE/FORCE RLS and policies; `app_rw` is declared `NOBYPASSRLS`. Migration 0047 adds global credential rows with forced RLS, no direct `app_rw` grants and narrow `identity_authenticator` functions. A full live-catalog table-by-table/grant audit remains open. |
| Checkpoint and vector isolation (4, 7, 18) | **Implemented in schema/code** | Migration 0002 applies tenant-prefix policies to LangGraph checkpoint tables and hash partitions/halfvec/HNSW to chunks. |
| Run lifecycle and durability (5, 18) | **Implemented with different trigger service** | `src/account_intelligence/runs/lifecycle.py`, workflow runner, task routes and tests cover reservations, state transitions, terminal settlement and resumability. Cloud Tasks is the dispatch/retry trigger, with LangGraph as state/checkpoint owner; no Inngest endpoint was found. |
| Billing units / settlement (9) | **Implemented; separate units represented** | `reserved_credits`, wallet ledger, `budget_usd`, `spent_usd`, cap version are distinct. Reservation/settlement is atomic and idempotent; terminal statuses settle in `finish()`/`settle_once()`. Tests exist for FAILED/refund, approval timeout, BUDGET_EXCEEDED and ledger invariants, but were not executed in this audit. |
| Idempotency (18) | **Implemented** | `RunRequest.request_hash()` is compared on replay in `runs/lifecycle.py`; mismatch maps to 409. Run creation and key bounds are in `api/routes/runs.py`. |
| Identity/roles (9, 18) | **Partial and divergent** | Tenant routes support verified bearer JWTs and HttpOnly DB-backed sessions, membership recheck and CSRF for cookie mutations. Role catalog includes `admin`, `analyst`, `seller`, `viewer`; analyst is already used, despite the older architecture's three-role baseline. Tenant membership management, per-session list/revoke and a local password login endpoint are implemented. Local account enrollment/linkage, email delivery, recovery and MFA remain absent. |
| Public API and errors (9) | **Mostly implemented** | OpenAPI snapshot, problem details, request IDs, retry headers, ETags, cursor endpoints, run approval and exports/views exist. Pagination is not uniform; accounts use domain continuation. No `/v1/me` capability/plan/MFA envelope was found. |
| Client-safe/operator-only output | **Partial; review required** | Separate tenant/operator route planes and operator spend views exist. Tenant run spend schemas and exports exist; verify every serializer/export for broker/provider and `cost_usd` leakage before production. No claim of a complete leak proof is made here. |
| Worker ingress/signatures | **Configured, deployment verification open** | Task handlers verify Google-signed OIDC unless dev/test explicitly allows unauthenticated tasks. No `/api/inngest` route. Compose publishes the worker task port locally by design; cloud ingress/service IAM/network rules were not verified in this read-only repository audit. |
| Product web screens | **Partial, substantial implementation** | Dashboard/account brief, alerts, audit, usage, settings/team/security, chat and operator screens exist. Dedicated flows required by prompt (first-party register/verify/reset/MFA, new research UX and all roadmap tables) are incomplete or absent. Read `web/app` for current route set. |
| CI/release/deployment | **Partial** | CI gates are strong and E2E exists. Dockerfiles and Compose exist; the optional Helm chart is linted/rendered in CI. Seeded two-tenant/four-role/operator environment and Schemathesis gate remain open. |

## Security review

### P0 — production auth boundary is incomplete

**Owner: Auth/backend.** The unsafe staff-password shim and its broad-grant migration were removed. A new narrow-scope local-auth migration 0047 now provides the credential store. Remaining gaps are:

1. Self-service tenant registration and local password recovery are not implemented. Credential enrollment is currently available only to an authenticated tenant SSO member; email verification is implemented but production delivery is not wired.
2. The production default IdP adapter and mailer fail closed until real adapters and credentials are wired; a configured connection alone does not make tenant sign-in live. Local MFA is not implemented, and admins requiring MFA cannot use local password sign-in.
3. The existing operator flow is separate and enforces Google Workspace allowlisting and recent MFA in the API. Initial operator bootstrap remains an environment/identity-owner operation; no public operator creation route exists.

**Required before production auth acceptance:** implement feature-flagged self-service tenant registration and password reset, wire a production mailer and identity secrets, add local MFA and refresh-token reuse detection, provision named operator allowlists and MFA, and configure the production domain. The current app does not return tenant bearer tokens to browser JavaScript.

### P1 — production-readiness/security gaps

- **Capability validation:** `/v1/me` now returns server-derived capabilities and the client uses it for navigation. Verify every capability against route policies and feature/plan limits before release.
- **Auth abuse controls:** tenant SSO has state/session/CSRF machinery. Local auth now includes Cloud Armor IP limits, Redis shared account limits in cloud, uniform login refusals, Argon2 dummy verification and progressive lockout. Device limits, production Redis verification and full operational alerting remain open.
- **Serializer leak proof:** client-safe export policy is explicit in docs and some schema names, but audit did not establish that every tenant API and export excludes provider names, per-call `cost_usd`, and broker credit counts. Make serializer-level tests a gate.
- **Header/edge coverage:** Next has nonce CSP and API responses set no-store; backend emits `nosniff`, frame deny and referrer policy. HSTS, COOP, Permissions-Policy, production ingress restrictions, CORS allowlist and signed task routing need an environment-level verification; no running cloud configuration was inspected.
- **RLS coverage:** migration 0047 is a direct exception. Also run the prescribed live catalog role/policy/grant inventory for every tenant table and confirm insert-only restrictions on audit and wallet ledgers. Static migration review alone is not a complete database proof.

### P2 — contract and delivery gaps

- The supplied roadmap describes an analyst approval screen, table-first product views and phased pilots; these add scope to the existing screen inventory. Its Temporal recommendation conflicts with Architecture v2.0 and current LangGraph + Cloud Tasks code; see `DECISIONS.md`.
- Contract is stored at `docs/api/openapi.json` rather than the requested `/contract/openapi.json`; generated client exists, but MSW fixtures and “no mock in production” CI check were not found.
- The optional Helm web chart now exists; cluster-specific GKE egress, ingress, External Secrets and Argo Rollouts integration still need environment validation. Same-digest Cloud Run staging-to-production remains the supported repository release workflow.
- Attached prompt calls for an auth role inventory/security ownership file, `SECURITY.md`, `SCREENS.md`, `SYNC.md`, and `RUNBOOK.md`; these task deliverables are not all present at repository root.

## Specific suspects checked

| Suspect | Result |
|---|---|
| Membership recheck only trusts JWT role | **Not true for tenant routes:** `require_role()` and router `authorize()` call membership cache (about 60 s) and compare current stored role; session revocation is checked on cookie sessions. Bearer-token session revocation beyond membership was not proven. |
| Terminal wallet settlement missing | **Not supported by source review:** terminal `finish()` calls `settle_once()` and writes commit/refund; code includes FAILED full refund and BUDGET_EXCEEDED partial settlement. DB integration tests exist but were not run. |
| Wallet credits mixed with USD cap | **Not supported by source review:** values are stored separately as reserved credits and USD budget/cost, with cap calculations using USD. |
| Idempotency replay ignores different payload | **Not true in current source:** canonical run request hash persists and mismatched reuse returns 409. |
| RLS/grants incomplete | **Local credential storage boundary added:** migration 0047 has forced RLS, `app_rw` receives no direct credential-table access, and the non-login resolver is NOBYPASSRLS with narrow column grants. Integration tests and migration lint cover these new functions; full live-schema verification remains outstanding. |
| `/api/inngest` signature/ingress | **Not present:** no Inngest route; Cloud Tasks task endpoints instead verify Google OIDC in configured environments. Verify deployed worker is not publicly reachable except through authenticated task ingress. |
| Missing endpoint catalogue items | **Mixed:** run list, approval, exports, views, audit, users, `/v1/me`, session listing/revocation and operator routes exist. First-party tenant auth/password/MFA and complete operator auth lifecycle are absent. |
| Problem details, `Retry-After`, ETag | **Implemented in source:** exception handlers shape HTTP/validation errors; typed rate-limit paths preserve Retry-After; GET run/progress support ETag/304. Snapshot has OpenAPI response metadata. |

## Production owner setup

Implementation follows the user's authorization to use the prompt defaults: local first-party auth remains SSO-ready; self-service signup is intended to be feature-flagged and require email verification; Stripe remains the documented top-up default. Before production, an owner still must provision the initial named operator with MFA, configure a public app/API origin, wire mail and identity secrets, and connect the payment account. These values are intentionally not inferred or committed.

## Implementation status

- Removed the untrusted staff login, token fallback, staff session UI, auth migration and route catalog entries; this work grants no global credential access to `app_rw`.
- Added `GET /v1/me`; the server-rendered workspace layout uses its validated capabilities and redirects unauthenticated users to sign-in.
- Tenant BFF forwards only the session/pre-auth cookies and an explicit header allowlist; it no longer forwards Authorization or arbitrary cookies.
- Operator BFF no longer fabricates tenant/flag/key results when upstream is unavailable. It requires a verified Workspace session and returns 503 on API outage.
- Added web `/healthz` and API-backed `/readyz` probes and an optional digest-pinned web Helm chart; operator OAuth secrets are mounted from files in the chart.
- Added current-user session listing and per-session revocation endpoints plus a security screen; PostgreSQL integration coverage checks ownership, CSRF and revocation behavior.
- Added a generated-contract-backed tenant run detail view, coverage outcomes, recent-run links and capability-gated approval/cancel actions; progress polling uses ETags and pauses when the tab is hidden.
- Added local Argon2id password policy primitives and migration 0047's isolated credential/token store, progressively locked failure counters, single-use verification, active-membership resolution and reviewable database role boundaries. Added SSO-member password enrollment, verification, resend and local-login HTTP flows with integration coverage. Self-service registration, password reset, production mail and local MFA remain open.
- Updated operator and tenant auth boundaries and regenerated OpenAPI/TypeScript client. Verification is reported with the task completion; CI remains the release gate.

## Audit evidence and limits

Reviewed source tree, OpenAPI snapshot, API routes/policy/guard/auth, run lifecycle and billing paths, tenant transaction helper, migrations, config, Compose, CI, web app route tree, and supplied architecture/production roadmap PDFs and PRD DOCX files. The repo was dirty on entry; changes outside the auth/UI implementation were preserved. No production database, ingress, secrets manager, cloud IAM, Stripe account, or live endpoint was accessed. No claim about production runtime behavior is made.
