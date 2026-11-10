# 02 · Audit by dimension (A–N) and answers to the 20 leads

Each dimension opens with what the concept is and why it matters here, then the current state with evidence, the verdict, the risk and the fix. Findings (`SD-nn`) are in `03-findings.md`. Labels: **V** verified, **I** inferred, **NF** not found. Working tree of `feat/final-product` with uncommitted changes, and live project `abmgenerator`, 2026-11-10.

Verdict scale: **Sound**, **Needs work**, **Missing**, **Could not verify**.

---

## A. Edge and networking — Needs work

**Concept.** The edge is everything between the internet and your code: the domain name, the TLS certificate, the load balancer that spreads requests, and the web application firewall (WAF) that drops attacks and floods. Behind it, a private network (VPC) keeps databases off the internet, and a fixed outbound address (NAT) lets partners allow-list you. Server-side request forgery (SSRF) protection stops your servers from being tricked into calling internal addresses such as the cloud metadata server.

**Current state.**
- Global external Application LB with HTTPS redirect, managed certificate and SSL policy; Cloud Armor with OWASP rules, rate-based bans and Adaptive Protection; CDN off (V `gcloud compute …`, `infra/modules/edge/main.tf:59-182`).
- Only `web` is reachable: api and worker have `internal` ingress and IAM invokers limited to one service account each (V `gcloud run services get-iam-policy`). A stray `my-service` has ingress `all` but no invoker binding, so unauthenticated calls are refused by Cloud Run IAM (V; SD-30).
- Direct VPC egress (all traffic) from all three services into a deny-all-ingress VPC; Cloud SQL and Redis on private IPs via Private Service Access; Cloud NAT with a reserved static IP `136.80.128.242` (V `gcloud compute routers list`, `addresses list`).
- No owned domain: `136-81-60-29.sslip.io` (V; SD-14).
- SSRF: the platform never fetches target pages itself; it asks providers to. `connectors/security.py:40-156` enforces https, refuses metadata hosts and IPs, requires every DNS answer to be public and the URL to be on the account allow-list, and re-checks redirects reported by providers. Tenant IdP calls use a pinned resolve-and-connect client (V `src/identity/ports.py:70-116`). Gap: Ask `read_web_page` skips the DNS and allow-list checks (V `replica/web_tools.py:231-266`; SD-15).
- The `default` VPC with open SSH/RDP rules still exists (V; SD-30).

**Risk.** The edge itself is good. The domain and the leftovers fail a review; the Ask gap is an egress channel.
**Fix.** SD-14 (own domain), SD-15 (Ask tools through the connector policy), SD-30 (cleanup).

## B. Routing and API design — Needs work

**Concept.** Routing decides which code answers which URL and who may call it. Good API design gives every route a version, validates input, returns errors in one shape, lets clients retry a payment-like request safely (idempotency keys), limits how fast one user or tenant can call, and sets timeouts on every hop so a slow dependency cannot hold resources forever.

**Current state.**
- 229 routes; every `/v1` and `/operator` route has exactly one catalog entry and unknown routes get 403 (V route table in `01-as-built.md` §12; `api/guard.py:64-69`; CI `scripts/check_route_catalog.py` via `make contract`, `ci.yml:94-95`).
- The web BFF is a route handler, not a rewrite; it forwards an allow-list of headers and three cookies, refuses cross-origin writes, and adds a Cloud Run ID token (V `web/app/v1/[[...path]]/route.ts:13-124`; `web/lib/serverless-auth.ts:65-119`). No CORS in the api; the browser never calls it directly (V).
- Validation with pydantic `extra="forbid"`; problem+json errors; OpenAPI snapshot checked in CI and turned into a zod client that validates both ways (V `api/main.py:531-648`; `scripts/export_openapi.py`; `web/lib/api.ts:105-160`). Error detail is stripped for `HTTPException` (SD-24).
- Idempotency: header on `POST /v1/runs`, body `request_id` on research/resume/downloads/Ask, dead shared module (SD-23).
- Rate limiting: Cloud Armor per IP; per-user Redis buckets on uploads, resume, exports, downloads; none on Ask or research start beyond monthly quotas (V `config/rate_limits.yaml:9-15`; `api/routes/ask.py:201-205`).
- Timeouts: proxy 120 s JSON, 60 min transfers, none for SSE; api and web Cloud Run 3600 s; LB fixed 60 min for serverless NEGs; Node fetch default 300 s header timeout caps transfers that are slow to start (V/I `web/lib/proxy-body.ts:18-28`; `infra/stack/main.tf:242-243`).
- Streaming: Ask SSE piped without buffering; the api middleware overwrites `no-transform` (V `route.ts:31-36,115-119`; `api/main.py:486`).
- Three pagination styles; signed-cursor module unused (V).
- Uploads: browser → GCS resumable session, CRC32C on completion; 20 GiB per file, no count or storage quota (V; SD-32).

**Risk.** Mostly consistency and operability; per-handler auth on two routers is the one latent security risk (SD-22).
**Fix.** SD-22, SD-23, SD-24, SD-32.

## C. Authentication — Needs work

**Concept.** Authentication proves who is calling. For a B2B product that means sign-in through each customer's identity provider (SSO) with protections against forged or replayed logins (state, nonce, PKCE), sessions stored server-side with safe cookie flags, CSRF protection for cookie-based calls, a second factor before dangerous actions (step-up MFA), and machine identities for service-to-service calls.

**Current state.**
- Per-tenant OIDC with state, nonce, PKCE S256, exact redirect URI, strict token checks; SAML with signed response and assertion, XML hardening and a replay table (V `src/identity/oidc.py:49-157`; `saml.py:45-238`).
- Password sign-in with Argon2id and lockout for non-admins; admins must use SSO with IdP-asserted MFA (V `auth.py:143-202`; `broker.py:396-414`).
- Server-side sessions, only a SHA-256 of the token stored; `__Host-` cookie, Secure, HttpOnly, SameSite=Lax; idle 30 min, absolute 8 h; rotation on sign-in; revocation on logout, password reset, role change (V `src/identity/sessions.py`; `config/session.yaml:9-26`).
- CSRF: HMAC of the session id in `X-CSRF-Token`, plus a same-origin check in the BFF (V `src/identity/csrf.py`; `guard.py:89-99`).
- Fake IdP and unauthenticated tasks are refused in cloud by config validation (V `config.py:505-511,598-604`).
- Tasks and ticks: Google OIDC token verified in code (audience + service-account email), on top of Cloud Run IAM (V `adapters.py:249-262`; `tasks.py:101-128`).
- Provider credentials: Secret Manager, per-secret accessor bindings, CMEK; rotation manual, versions pinned to `latest` (V `infra/modules/secret_manager/main.tf:36-83`).
- Gaps: SSO domain claims unverified (SD-10); staff MFA assumed for Google and personal accounts in staff and owner roles (SD-13); step-up missing on some admin actions (SD-21).

**Risk.** The core is well built. The gaps are exactly the ones an enterprise SSO review asks about.
**Fix.** SD-10, SD-13, SD-21.

## D. Authorisation and multi-tenancy — Needs work

**Concept.** Authorisation decides what a signed-in caller may do. Multi-tenancy means many customers share one system, so every query, file, cache entry and search must be fenced to one tenant. Row-level security (RLS) makes the database itself enforce that fence, so an application bug cannot read another tenant's rows. An audit log records who did what, and should be tamper-evident.

**Current state.**
- Default-deny route catalog; roles admin/analyst/seller/viewer; membership re-checked every request with a 60 s cache (V `api/policy.py`; `api/guard.py:72-117`; `api/membership.py`).
- RLS: all 186 tables with `tenant_id` have `ENABLE` + `FORCE` + a policy on `app_tenant()`; context set per transaction with `set_config(..., true)` so pooling is safe; runtime roles `app_api`/`app_worker` are `NOBYPASSRLS`; ticks run as `app_worker` with per-tenant transactions; cross-tenant lookups go through reviewed `SECURITY DEFINER` functions (V `migrations/0002_rls.sql:9-113`; `0101_service_roles.sql:36-140`; `db/session.py:204-252`).
- Private workspaces add `owner_user_id` rules; `app.scope='system'` is set by the app, so it protects against app bugs, not against SQL access by the app role (V `0063_private_workspaces.sql:96-99`; I).
- Buckets: every object key carries the tenant; content addressing is per tenant; downloads are streamed by the api after an RLS lookup and a key-owner check; no signed URLs (V `src/tenancy/storage.py:183-220`; `exports/store.py:122`).
- Cache keys carry the tenant; the vector table is hash-partitioned by tenant with RLS (V `src/tenancy/keys.py:13-21`; `0002_rls.sql:88-113`). The platform library is visible to any session with no tenant set (V `0053_replica.sql:275-288`; platform content, low risk).
- Support grants: tenant-approved, one named operator, ≤120 min read / 30 min write, audited per call (V `src/admin/support.py:51-85`; `api/operator.py:268-339`). Bypass through the invite link (SD-11).
- Audit log: per-tenant hash chain, insert-only grants, read events written before data returns; not tamper-evident, verification capped (SD-12).

**Risk.** Tenant isolation in data is **sound**; the weaknesses are in staff access and evidence.
**Fix.** SD-11, SD-12, SD-22.

## E. Service architecture — Needs work

**Concept.** Service architecture is how work is split between processes and how long work survives failures. Queues (Cloud Tasks) deliver "at least once", so every handler must be safe to run twice (idempotent). Leases decide which worker owns a job, and a fencing token stops an old owner that wakes up late from writing. A sweeper finds stuck work. A state machine with enforced transitions prevents impossible states.

**Current state.**
- api/worker split by `SERVICE_ROLE`; `all` refused in cloud; images default to safe roles (V `api/planes.py:37-84`; Dockerfiles).
- Synchronous: everything user-facing including Ask streams (≤3600 s) and account object erase (fire-and-forget, SD-31). Queued: research jobs, slices, documents, downloads, approval timeouts (V `adapters.py:86-226`).
- Idempotency under redelivery: named tasks; atomic job claim; run created under an idempotency key; per-call ledger with `(run_id, args_hash)` uniqueness; same-process redelivery guard; Postgres lease row with epoch fencing on paid calls; `begin_slice` compare-and-set (V `runner.py:104-278`; `ledger.py:233-323`; `credits.py:463-475`).
- Gaps: epoch does not fence checkpoint or status writes (I, `platform/checkpoint.py:121-131`); runs between slices can be ended or lose their next slice (SD-08); 8 s shutdown; concurrency 4 (SD-25).
- State machine: transitions validated in code with compare-and-set; DB trigger only blocks leaving terminal states (V `runs/transitions.py:32-65`; `0010_run_integration.sql:59-85`).
- Repeatedly failing tasks: dropped after `maxAttempts` (8/5/20); no dead-letter record; 404 for deleted items is retried to exhaustion (I).
- Slice loop: 1650 s within an 1800 s dispatch deadline and 3600 s Cloud Run timeout, CPU always on for the worker; 150 s margin for setup and hand-off (V; lead 11).
- Two engines and unwired packages (SD-28).

**Risk.** The core idempotency design is good; the hand-off between slices is where runs are lost.
**Fix.** SD-08, SD-25, SD-28, SD-31.

## F. Data layer — Needs work

**Concept.** The data layer is the schema, how it changes (migrations), how transactions keep money and state consistent, how connections are pooled, and how data is backed up, recovered, retained and deleted. For personal data, deletion must reach every copy.

**Current state.**
- 222 tables, 114 migrations in one Alembic chain of SQL files, run by a Cloud Run job before services roll; forward-only in practice; all revisions in one transaction (V `migrations/env.py:69-83`; `staging_release.py:245-257`). Silent zero-row DML under forced RLS (SD-18).
- Transactions: one per unit of work, READ COMMITTED; money paths use row locks and unique keys (V `db/session.py:204-252`; `credits.py:295-408`). Lock-order deadlock between Stop and settle (SD-34).
- Pooling: SQLAlchemy async + asyncpg via the Cloud SQL connector, 5 + 5 per process, one process per instance; checkpointer opens one extra psycopg connection per slice; no pooler (V `infra/stack/main.tf:262,347`; `platform/checkpoint.py`). Worst case ≈ 30 (api) + 42 (worker) + jobs ≈ 75 of 200 (I; lead 8).
- pgvector: HNSW per tenant partition, RLS filter (V).
- Growth: only `chunks` partitioned; ledgers, checkpoints and events kept forever (SD-29).
- Backups/HA: zonal, 7 backups, 7-day PITR, no cross-region (SD-06). No timeouts, slow log off (SD-19).
- Retention and deletion: account purge works in the DB; objects erased fire-and-forget; tenant plan cascades the ledger (SD-01); no DSAR, no retention job (SD-04).
- Buckets: versioning on data bucket, noncurrent 7 d, soft delete 7 d, raw payloads 30 d, quarantine 1 d (V `gcloud storage buckets list`).
- Redis: rate buckets, cache index (with response content), no sessions or money (V; SD-07).

**Risk.** One P0 (ledger cascade) and the deletion gap dominate.
**Fix.** SD-01, SD-04, SD-06, SD-18, SD-19, SD-29, SD-31, SD-34.

## G. External integrations — Needs work

**Concept.** Every outside service fails sometimes. Each call needs a timeout, bounded retries with backoff, a rate limit that respects the provider's quota, a circuit breaker that stops hammering a failing service, a fallback or a clear failure, and a record of cost. You should also know exactly what data you send to each one.

**Current state.** See the table in `01-as-built.md` §2.5.
- Research provider calls go through one pipeline: single-flight, strict args, URL policy, platform-wide governor, ledger claim with replay, breaker, Redis limiter, bounded HTTP, cost finalisation (V `connectors/service.py:133-602`). This is a strong design.
- Breaker state is per instance; model calls have no breaker (SD-26). Ask web tools bypass the pipeline (SD-15).
- Model providers: no fallback by owner decision; Anthropic key read from Secret Manager at start, so rotation needs a new revision (V `container.py:708-725`).
- Notification channel: none (mail removed; Slack/webhook channels raise NotConfigured) (V `src/alerts/channels/__init__.py:40`).
- Payment provider: none (SD-20).
- Embeddings and malware scanning are local (V).

**Risk.** Research calls are well protected; the edges (Ask, models, notifications, billing) are not.
**Fix.** SD-15, SD-20, SD-26.

## H. Language-model architecture — Needs work

**Concept.** Model calls need routing (which model for which task), pinned versions so behaviour does not change underneath you, structured output that is validated, limits on tokens and cost, caching, and evaluation. Anything the model reads from the web or a file is untrusted and may contain instructions (prompt injection), so the tools a model can call must be limited to what is safe even if the model is fooled.

**Current state.**
- One config file holds model ids and prices; tasks map to profiles; users can choose research and analysis models (V `config/models.yaml:1-367`).
- Ids are aliases, not dated snapshots (SD-36).
- Structured output via JSON schema with refusal detection; Ask final answer shape-checked (V `platform/llm.py:690-720`; `replica/tools.py:9-11`).
- Tools: research calls use no model tool-use; Ask tools are read-only (table, passage, archive reads in `tenant_txn`; web search/read; `run_python` in a seccomp sandbox behind a tenant flag); markdown sanitised and CSP blocks remote images (V `replica/tools.py`; `web/components/ask/AnswerMarkdown.tsx`; `web/proxy.ts:23`). Damage from injection is limited to reading the caller's own tenant data and sending model-chosen text to the search provider (SD-15).
- Limits: caps and token guard off; no Ask output cap; thread spend not enforced (SD-02).
- Caching: prompt caching with a warm gate; reply cache in Postgres (V `platform/llm.py:652-690`; `container.py:289-297`).
- Evals: replay on every PR; nightly live tabular eval on Kimi (V `.github/workflows/eval.yml`; whether it runs: Could not verify).
- Data leaving per provider: data-class guard off (SD-05).

**Fix.** SD-02, SD-05, SD-15, SD-36.

## I. Scalability and performance — Needs work

**Concept.** Scalability is how far the system grows before something saturates, and which thing saturates first. On Cloud Run that is set by instance limits, requests per instance (concurrency), CPU allocation and cold starts, and behind them by the database, queues and provider quotas.

**Current state (V `gcloud run services list`, `tasks queues list`).** web and api: min 1, max 3, concurrency 40, CPU only during requests, gen1. worker: min 0, max 3, concurrency 4, 4 vCPU, CPU always on. Runs queue: 6 concurrent dispatches.

**Rough capacity today (I, arithmetic from the settings above).**

| Resource | Ceiling | First to bind when |
|---|---|---|
| Concurrent slices (research runs in progress) | 6 (runs queue) of 12 worker slots | the 7th active run (SD-08 then ends runs) |
| Provider calls in flight, platform-wide | 4 per provider, 8 for search; a tenant ≤ 50 % | several runs in the same lane |
| Concurrent web/api requests | ≈ 120 per tier (3 × 40) | ≈ 40 users each with a run page open (SSE + polling) plus active Ask streams |
| Database | 1 vCPU; ≈ 75 of 200 connections used at full scale-out | heavy reads (2026-11-01) or checkpoint writes from 6 slices |
| Ticks | sequential per tenant, 120 s for metrics | tens of tenants |
| Requests per second at the edge | Cloud Armor bans > 1200/min per IP | not a concern |

**First three bottlenecks:** (1) the runs queue and worker size (6 slices), (2) platform-wide provider ceilings, (3) the 1-vCPU database. Cold starts: worker min 0 with a large image (LibreOffice, ONNX model) delays the first slice after idle (I).

**Fix.** SD-16, SD-19, SD-33, SD-06.

## J. Reliability — Needs work

**Concept.** Reliability is how the system behaves when parts fail: whether there are single points of failure, whether work survives a crash, whether money is counted exactly once, and how much data and time you lose in a disaster (recovery point objective, RPO, and recovery time objective, RTO).

**Current state.**
- Single points of failure: zonal Cloud SQL, BASIC Redis, one region (V; SD-06, SD-07).
- Dependency failures: see the failure view in `01-as-built.md` §10.
- Graceful shutdown: 8 s, then resume from checkpoint (SD-25).
- Exactly-once money: reservation and settlement are effectively exactly-once (row lock + unique keys + status checks); paid calls are at-most-once-charged per `(run, args)` with a flagged duplicate in a crash window (V `credits.py:295-408`; `ledger.py:18-41`). Spend beyond reservation is not charged (SD-02).
- Disaster recovery today (I from settings): RPO ≈ minutes within the region (PITR), up to 24 h if the region is lost and backups are unreadable there; RTO hours (manual restore, no runbook drill evidence). No multi-region position.
- Nobody is paged (SD-03).

**Fix.** SD-02, SD-03, SD-06, SD-07, SD-08, SD-25.

## K. Security — Needs work

**Concept.** Security here means secrets kept out of code and rotated, each service account allowed only what it needs (least privilege), untrusted files handled in a sandbox, browser security headers, scanning dependencies and images, and handling personal data lawfully.

**Current state.**
- Secrets: Secret Manager with CMEK, per-secret access; no real secrets in tracked files or history (V `git grep` patterns; `.gitignore:15,37,49`; CI gitleaks `ci.yml:241-245`). Rotation manual (I).
- Least privilege: dedicated SAs with narrow roles; but default SAs hold Editor and personal accounts are Owners (SD-13, SD-30).
- Uploads: content sniffing, macro/executable guard, clamd fail-closed, sandboxed parsing with seccomp, rlimits, no network, nobody user (V `documents/scan.py`; `documents/sandbox_child.py:11-185`). LibreOffice outside the sandbox (SD-38).
- Headers: strict nonce CSP, HSTS, frame denial, no-referrer on web; api adds its own (V `web/proxy.ts:16-39`; `web/next.config.ts:16-34`; `api/main.py:476-489`).
- Images: digest-pinned bases, non-root; Trivy in CI, but scanning disabled in the registry and CI does not gate releases (SD-09, SD-35).
- Personal data: people data from providers; docs are drafts for counsel and disagree with code; no DSAR, no retention (SD-04, SD-05).

**Fix.** SD-04, SD-05, SD-13, SD-30, SD-35, SD-38.

## L. Observability and operations — Missing

**Concept.** Observability is being able to tell, from outside, what the system is doing: structured logs, metrics, traces that follow one request across services (a correlation id), and error tracking. Operations turns that into action: alerts routed to a person, service-level objectives (SLOs) that define "broken", runbooks, and cost monitoring.

**Current state.**
- Structured JSON logs with allow-listed fields; request id in api; OpenTelemetry metrics and Cloud Trace export on (V `src/obs/logging.py:1-81`; `obs/tracing.py`; env `METRICS_EXPORT=cloud_monitoring`).
- Correlation breaks at Cloud Tasks; trace field not linked; worker missing from the 5xx alert (SD-27).
- 17 alert policies, 0 notification channels; workload/SLO alerts off; no error tracker; mail removed (SD-03).
- SLOs and ~40 runbooks exist on paper (V `config/telemetry/slos.yaml`; `docs/runbooks/`), with no channel to act on them (I).
- Cost: GCP budget email only; provider and model spend visible in-app, not alerted (V; SD-02).

The telemetry is there; the loop from "something is wrong" to "a person knows" does not exist, so the verdict is **Missing**.

**Fix.** SD-03 first, then SD-27.

## M. Delivery — Needs work

**Concept.** Delivery is how code gets to production: separate environments that look alike, configuration and feature flags kept in one place, a pipeline (CI/CD) that tests and scans exactly what ships, infrastructure described as code (Terraform) that matches reality, and a tested way to roll back.

**Current state.**
- One environment, used for customers and tests; caps off there (SD-17).
- CI is extensive (lint, static analysis, contract, migrations, isolation, 94 % coverage, web tests, Playwright, audits, Trivy, SBOM) but does not gate what is deployed (SD-09).
- Terraform covers the whole stack (16 modules); deployed services were last changed by `gcloud`; drift not measured (V `gcloud run services list` annotations; Could not verify the plan).
- Rollback: release script reverts service revisions; database forward-only (V `staging_release.py:14-21`).
- Flags: env switches per service plus per-tenant DB flags (V `api/flags.py`).
- Tests of critical paths exist for money, isolation, idempotency and runs (V `tests/` layout; not run in this review).
- Docs drift (SD-37).

**Fix.** SD-09, SD-17, SD-37.

## N. Frontend architecture — Sound

**Concept.** The frontend's job in a secure design is to render, not to decide. Authorisation must be enforced by the API on every call; the browser must not see secrets or call internal services directly; long-running work needs honest progress and recovery from dropped connections.

**Current state.**
- Next.js 16 App Router; server components call the api with a 10 s timeout; client components use a generated, validated API client (V `web/lib/server-api.ts`; `web/lib/api.ts`).
- Auth enforced by the api on every call; the web layout only redirects to sign-in (V `web/app/(client)/layout.tsx:11-52`).
- No `NEXT_PUBLIC_*` variables; the api origin is server-only; CSP `connect-src 'self'` plus the upload origin (V).
- Long work: SSE with reconnects and polling fallbacks; Ask over fetch-SSE with abort; uploads resumable across reloads (V `web/lib/useRunStream.ts`, `useResearchLive.ts`, `sse.ts`, `upload-store.ts`).
- Minor notes, not findings: GCS session URL kept in `localStorage` (mitigated by nonce CSP); possible duplicate `Set-Cookie`; `Content-Length` dropped on downloads; the operator proxy has no upstream timeout (V `route.ts:100-113`; `app/api/operator/v1/[...path]/route.ts:84-91`).

---

## Answers to the 20 leads

| # | Lead | Answer | Evidence |
|---|---|---|---|
| 1 | Edge: domain, TLS, LB, WAF, CDN, ingress; can api/worker be reached from the internet? | Global HTTPS LB with managed TLS, Cloud Armor WAF + Adaptive Protection; no CDN; no owned domain (sslip.io). web ingress internal+LB; api and worker `internal` with IAM invokers limited to web SA and tasks SA respectively. **api and worker are not reachable from the internet.** Finding SD-14 (domain). | V `gcloud run services list`, `get-iam-policy`, `compute …` |
| 2 | Task/tick auth; can a wrong SERVICE_ROLE expose task routes? | OIDC token minted for `ai-staging-tasks`, custom audience `ai-staging-worker-tasks`; verified in code (signature, audience, email, email_verified) and by Cloud Run IAM. `/tasks` is registered only for `worker`; `all` refused in staging/production; images default to `api`/`job`. Exposure needs several misconfigurations plus a valid token. **Confirmed non-issue.** Hardening: SD-22. | V `adapters.py:249-262`; `tasks.py:101-128`; `planes.py:37-84`; `gcloud scheduler jobs list` |
| 3 | The /v1 pass-through | Next.js route handler; allow-listed headers and three cookies; CSRF header forwarded; XFF forwarded but untrusted by uvicorn, so the api never knows the client IP (per-IP limits only at Cloud Armor); browser never calls api; no CORS; SSE piped unbuffered with no deadline; JSON 120 s, files 60 min (capped at 300 s time-to-headers by Node defaults, I); LB 60 min fixed; api/web 3600 s. Findings SD-33, minor notes in N. | V `route.ts:13-124`; `proxy-body.ts:18-28`; Google docs on serverless NEG timeout |
| 4 | Uploads | Browser → GCS resumable session directly, 32 MiB chunks; admin/analyst/seller only; per-user bucket 120 tokens/0.5 s; 20 GiB per file; **no count or storage quota**. SD-32. | V `documents/service.py:349-493`; `config/research_input_modes.yaml:7-14` |
| 5 | Private networking and egress IP | Direct VPC egress (no connector); Cloud SQL private IP only (IAM auth via Cloud SQL connector); Redis via PSA with TLS; **fixed egress IP 136.80.128.242** via Cloud NAT. **Sound.** | V `gcloud run services list`, `sql instances list`, `routers list` |
| 6 | Outbound fetch / metadata server | Providers fetch, not the platform; URL guard blocks non-https, metadata hosts and IPs, non-public DNS answers, off-allow-list hosts, and re-checks redirects; own clients do not follow redirects. Gap: Ask `read_web_page` skips DNS/allow-list (bounded to this turn's search results). SD-15. | V `connectors/security.py:40-156`; `connectors/http.py:43-73`; `replica/web_tools.py:231-266` |
| 7 | RLS role, FORCE, pooling, background work | Runtime roles `app_api`/`app_worker` (IAM users), NOBYPASSRLS, own nothing; FORCE on all 186 tenant tables; tenant context per transaction (`set_config(…, true)`), safe with pooling; checkpointer uses a session setting on a dedicated connection closed per slice; ticks and tasks run as `app_worker` with per-tenant transactions or definer functions, never a bypass role. **Sound.** Side effect: migrations' DML sees no rows (SD-18). Live roles: Could not verify (DB stopped). | V `0002_rls.sql`; `0101_service_roles.sql:36-140`; `db/session.py:204-252` |
| 8 | Connections | 5 + 5 per process, 1 process per instance, + 1 checkpointer connection per running slice. Max: api 3 × 10 = 30; worker 3 × (10 + 4) = 42; jobs ≈ 3 → ≈ 75 of `max_connections` 200. No pooler; the Cloud SQL connector is used. **Fine today**; a pooler becomes necessary when instances grow past ≈ 15 total (I). | V `gcloud run services list` env; `gcloud sql instances list` flags |
| 9 | Redis usage and failure | api and worker both use it: provider rate buckets and cooldowns (worker), research-cache index (worker, with response content), sign-in and per-user buckets (api). Down: paid calls fail closed, per-user actions 503, sign-in falls back per instance, cache misses. SD-07. | V `connectors/ratelimit.py:112-184`; `connectors/cache.py:137-276`; `identity/rate_limit.py:84-194` |
| 10 | Idempotency of tasks; lease safety; queue settings; failing tasks | research-job: named task + atomic claim + run idempotency key → idempotent. run-slice: named task, in-process guard, Postgres lease with epoch, `begin_slice` CAS; a concurrent redelivery gets 503 and retries. Queue settings in `01-as-built.md` §2.4. Failing tasks are dropped after max attempts with no dead-letter record. Edge cases that lose runs: SD-08. | V `runner.py:104-333`; `ledger.py:233-323`; `gcloud tasks queues list` |
| 11 | 1650 s slice vs timeouts | Cloud Run timeout 3600 s; dispatch deadline 1800 s; CPU always allocated on worker; concurrency 4 (should be 1); SIGTERM grace 8 s then cancel and resume from checkpoint. Margin 150 s for setup and hand-off. A slice that overruns 1800 s is retried while running (SD-08). | V `adapters.py:97-101`; `gcloud run services list`; `infra/stack/main.tf:645-646` |
| 12 | Double reservation or settlement | **No.** `client_settings FOR UPDATE` serialises reservations; repeat `request_id` returns the first job (unique index); one open job per account; atomic claim; settlement locks the job, checks status, inserts with `ON CONFLICT DO NOTHING`. Reservation, ledger entry and audit are in one transaction. Body-level `request_id`, not an `Idempotency-Key` header. Minor: SD-23, SD-34. | V `credits.py:245-408,463-475`; `0063_private_workspaces.sql:1729`; `0053_replica.sql:131` |
| 13 | Storage paths and download auth | Deliverables are at `<tenant>/<account>/<run>/deliverables/<sha>.<ext>` (the flow map's `deliverables/sha256.ext` is the tail only). Every bucket key carries the tenant; one data bucket for all tenants with SA-only IAM, UBLA and public access prevention; downloads streamed by the api after an RLS lookup and `require_owned(tenant, key)`; download parts need a per-job token too. **Sound.** | V `exports/store.py:122`; `tenancy/storage.py:183-220`; `gcloud storage buckets list` |
| 14 | Vector search | HNSW per partition on 16 tenant hash partitions; tenant filter by RLS (tenant + owner); account filter in SQL. No path returns another tenant's passages. **Sound.** Post-filtering can lower recall (I). | V `0002_rls.sql:88-113`; `0074_retrieval_indexes.sql:11-20`; `documents/retrieve.py:94-121` |
| 15 | LLM safety and data leaving | Research calls have no tools; Ask tools are read-only and tenant-scoped; `run_python` sandboxed behind a flag; output sanitised. Damage from injection: reads within the tenant, and model-chosen text sent to Parallel. Data sent: scraped text, people data, whole files and tables to Anthropic and (when chosen) Ollama Cloud; guard off; ZDR unverified. SD-05, SD-15. | V `replica/tools.py`; `replica/web_tools.py`; `config/models.yaml:202-290` |
| 16 | What tells a human production is broken | **Nothing**, except billing budget emails. Structured logs, metrics, Cloud Trace exist; correlation stops at Cloud Tasks; no error tracker. SD-03, SD-27. | V `gcloud beta monitoring channels list` → `[]` |
| 17 | Retention and deletion | Retention not enforced; account purge erases DB rows and (fire-and-forget) objects; tenant offboarding is a proposal that would cascade the ledger and keeps people data; no DSAR. SD-01, SD-04, SD-31. | V `infra/stack/main.tf:817-820`; `config/deletion.yaml` |
| 18 | Unnamed components | Embedding: local fastembed (present). Malware scanner: clamd sidecar (present, fail closed). Word rendering: python renderer + LibreOffice for checks (present, outside sandbox SD-38). Notification channel: **missing**. Payment/invoicing: **missing** (SD-20). CI/CD: GitHub Actions present but not used for releases (SD-09). IaC: Terraform present, drift unmeasured. Migrations: Alembic job (present). Backups: Cloud SQL automated (present, short, SD-06). | see `01-as-built.md` §2 |
| 19 | Two engines and unwired packages | Workflow v1 reachable only for tenants with an explicit `abm_agents` off rule; both share the slice runner and v1's config file; unwired refresh, signals, alerts, governance, evalgate; duplicate spend routes; dead api modules. Cost: double testing and review surface, misleading docs. SD-28. | V `wiring.py:341-378`; import-closure scan |
| 20 | Throughput ceiling | Real limits: 6 concurrent slices; provider ceilings 4/8 platform-wide; 1-vCPU DB; scheduled starts off. First bottleneck: the runs queue/worker size, which then triggers SD-08. SD-16. | V `gcloud tasks queues list`; `config/parallel.yaml:96-136`; `replica/schedules.py:221-224` |

## Items that could not be verified

| Item | Why | Command or access needed |
|---|---|---|
| Live database roles, `relforcerowsecurity`, IAM DB users, real connection counts | Cloud SQL instance stopped | Start the instance, then `gcloud sql users list --instance ai-staging-pg`; `psql -c "\du"`; `SELECT relname FROM pg_class WHERE relrowsecurity AND NOT relforcerowsecurity;`; `SELECT count(*), usename FROM pg_stat_activity GROUP BY 2;` |
| Whether the 16 data migrations applied their rows (SD-18) | DB stopped | Per migration, a `SELECT` for the condition its `UPDATE` targeted |
| Terraform drift | `terraform plan` not run (needs state and may need write locks) | `cd infra/envs/staging && terraform plan -lock=false -refresh=true` by the owner |
| Billing budgets and amounts | No billing-account permission | Billing admin: `gcloud billing budgets list --billing-account=<id>` |
| Model providers' retention terms (ZDR) | Contractual | Anthropic and Ollama account settings and agreements |
| seccomp TSYNC under Cloud Run gen2 | Needs a run in the cloud | Run the sandbox self-test on a worker revision and read its log |
| Ask SSE compression through Next.js; 32 MiB HTTP/1 limit on library upload; 2 GiB download parts; Node 300 s header timeout | Behaviour needs live requests | Staging tests with `curl -N` and a 100 MB library upload |
| Whether the nightly eval workflow runs | GitHub state | `gh run list --workflow eval.yml` |
| Organisation-level policies and Security Command Center | Project has no project-level org policies; org view needs org permissions | Org admin: `gcloud resource-manager org-policies list --organization 739140005194` |
