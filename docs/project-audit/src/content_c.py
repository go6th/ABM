"""Parts 3 and 4: code, website, API, database, cloud, release, security (chapters 15-21)."""
from kit import *
import figs_core as FC
import figs_more as FM


def build():
    h = []
    h.append(part("Part 3  The code, the website and the API", "Where everything lives, what the screens are, and every kind of server route."))

    # ---------------------------------------------------------------- 15
    h.append(chapter("15", "Where things live in the folder",
                     "About 2,900 files are tracked. This map tells you which folder to open for which question."))
    h.append(table(["Folder", "What is in it"],
        [["`src/`", "All Python (about 161,000 lines). One big package `account_intelligence/` plus 37 small top-level packages."],
         ["`web/`", "The Next.js website (about 24,000 lines of TypeScript)."],
         ["`config/`", "About 75 YAML files: rules the code reads as data (models, prices, grading, checklist, providers, security policy). Plus `config/skill/` (the owner's research skill) and `config/hermetic/` (fakes for tests)."],
         ["`migrations/`", "96 numbered SQL steps (head 0097; 0069 does not exist), each wrapped by a small Alembic file."],
         ["`infra/`", "Terraform: 15 modules describing Google Cloud."],
         ["`docker/`, `compose*.yml`", "Image definitions and local stacks (hermetic, live-and-paid, replica)."],
         ["`tests/`", "501 test files, about 120,000 lines, synthetic data only."],
         ["`scripts/`, `ops/`, `evals/`, `runtime/`", "Gates and generators, local ops tools, golden evaluation data, Word/PowerPoint render tools."],
         ["`docs/`", "354 Markdown files: ADRs, runbooks, specs, audits. `docs/guide/` is the previous overview (29 Oct)."],
         ["`unwanted-things/`", "A parking lot of files moved out of the live tree, never deleted (504 MB). Nothing imports it."],
         ["`.claude/`, `.agents/`", "AI-agent skills. Two of them are real build tooling that `make check` and pre-commit depend on."]],
        [24, 76]))
    h.append(h2("The Python packages"))
    h.append(table(["Package", "Purpose", "Lines"],
        [["`account_intelligence/api`", "The web server: routes, guard, policy catalogue, auth, error shapes, response contract.", "19,500"],
         ["`account_intelligence/replica`", "Everything behind the customer screens: jobs, credits, live view, Ask, tables, downloads, schedules, library.", "14,500"],
         ["`exports`", "Builds Word, Excel and zip files; verification; the Download-everything repair stage.", "23,300"],
         ["`documents`", "Upload intake, scanning, sandboxed parsing, redaction, chunking, tables, file-mode research, the quality gate.", "10,200"],
         ["`agents`", "The research graph, its guards G1-G17, prompts, pull and extraction logic.", "10,100"],
         ["`parity`", "The exact-copy rules for the delivered file bundle.", "6,800"],
         ["`account_intelligence/connectors`", "The doorway to every data provider: ledger, cache, breaker, rate limit, SSRF shield.", "6,500"],
         ["`people`, `jobs`", "The people-list pipeline and the job-postings pipeline.", "6,000 / 4,000"],
         ["`spend`", "Credit caps: propose, approve, block, pause, report.", "4,500"],
         ["`account_intelligence/workflow`, `runs`", "The slice engine (`runner.py`), the old 13-stage Workflow v1, the run state machine and sweeper.", "3,800 / 1,600"],
         ["`account_intelligence/grading`, `tracker`", "Turns a claim into a graded fact; the 33-row checklist and completion verdict.", "3,500 / 2,000"],
         ["`llm`, `account_intelligence/platform`", "Model adapters and the (dormant) Router; Google adapters, checkpoint, prompt cache.", "2,700 / 2,300"],
         ["`identity`, `tenancy`, `audit`, `admin`", "Sign-in, sessions, CSRF, rate limits; tenant settings; the hash-chained audit log; the staff plane.", "2,400 / 1,600 / 600 / 500"],
         ["`obs`", "Logging, metrics, tracing, the stall dump that writes every thread's stack when the loop freezes.", "2,000"],
         ["`chat`, `rag`", "Private chat threads; a retrieval toolkit (the RAG graph itself is unused).", "1,900 / 2,700"],
         ["`refresh`, `signals`, `alerts`", "The monitoring engine: built, tested, **not wired**.", "1,400 / 700 / 1,100"],
         ["`security`, `metrics`, `registers`, `unitcost`", "**Stubs** of 13-33 lines each; `security` always reports all clear.", "~100"]],
        [28, 58, 14]))
    h.append(p("Packages may import only in one direction (checked by `import-linter` with 17 contracts), so lower layers never import higher ones. The newest and largest packages, `replica` and `documents`, have no contract."))
    h.append(end_chapter())

    # ---------------------------------------------------------------- 16
    h.append(chapter("16", "The website: every page",
                     "A Next.js 16 app with the App Router. 52 page files: 34 real screens and 18 that only redirect."))
    h.append(fig(FM.fig_web(), "Map of the website. The customer layout is the only login check for workspace pages.", wide=True))
    h.append(table(["Address", "What the screen does", "Main API calls"],
        [["`/` (Ask)", "Question box with account scope, model and effort chips, file upload, streamed cited answers, token meter.", "`POST /v1/chat/ask` (SSE), `GET /v1/me/limits`, `/v1/models`"],
         ["`/accounts`, `/accounts/add`", "List of accounts. Add one three ways: research a new company, add a ready-made Library account, analyse your own files.", "`GET /v1/accounts`, `POST /v1/research`, `POST /v1/accounts/from-library`"],
         ["`/accounts/[domain]`", "The account workspace: KPI tiles, progress or Resume, the one-minute version, key findings, delivered files, a Deep dive rail with 11 links, an account-scoped Ask box.", "`GET /v1/accounts/{d}/workspace`, `.../parity/screen`, documents, download-everything"],
         ["`/accounts/[d]/[sheet]`", "Deep-dive table (people, signals, jobs, technology, news, mail-and-drive, sources, searches, facts, coverage) with server-side sort, filter and paging, CSV and Excel download.", "`GET /v1/accounts/{d}/sheets/{sheet}`"],
         ["`/accounts/[d]/archive`, `/files`, `/refresh`", "Stored copies of pages and job postings; analysis of your uploaded file; refresh scope and estimate.", "`/archive`, `/file-analysis`, `POST /refresh`"],
         ["`/research`, `/research/new`", "The live Research board (server-sent events with polling fallback); start new research by company name or file upload.", "`GET /v1/research/live[/stream]`, `POST /v1/research`, `/v1/documents/*`"],
         ["`/runs/[run_id]`", "One run's steps, files, gate verdict and coverage; Stop and Refresh. Uses the old design vocabulary.", "`GET /v1/runs/{id}`, SSE `/events`"],
         ["`/schedules`", "Per-account refresh cadence and monthly estimate.", "`GET /v1/schedules`, `PUT /v1/schedules/{id}`"],
         ["`/mail-and-drive`", "Connect the user's own read-only Gmail, Drive, Outlook, OneDrive, SharePoint.", "`/v1/mail-and-drive/*`"],
         ["`/model-preferences`, `/chats`", "Default research and analysis model and effort; list and delete chat threads.", "`PUT /v1/me/model-preferences`, `/v1/chat/threads`"],
         ["`/team`, `/support-access`", "Admin only: invite teammates and set people-data consent; give a named 6th staffer time-boxed read-only access.", "`/v1/users/*`, `/v1/support/*`"],
         ["`/login`, `/forgot`, `/reset`, `/invite`, `/verify-email`", "Sign-in and account flows, no session needed. SSO is a button that discovers the company's login.", "`POST /v1/auth/*`, `/v1/sso/*`"],
         ["`/operator/*`", "Staff console: overview, clients, users, the shared Library, runs, defaults, \"view as a client\". Signed in with Google; the gate runs in the browser.", "`/operator/v1/*` through `/api/operator/v1`"]],
        [22, 50, 28]))
    h.append(h2("How the browser reaches the API"))
    h.append(ul([
        "The browser talks only to its own origin. `/v1/**` goes through a **streaming proxy** (`app/v1/[[...path]]/route.ts`). The comments call it a rewrite, but `next.config.ts` has no rewrites.",
        "The proxy refuses non-GET requests whose `Origin` is not the site, forwards only a header allow-list and only the three `__Host-` auth cookies, caps body size (1 MiB JSON, 21 GiB for file transfers) and times out after 120 s (60 min for transfers). **Uploads go straight to object storage**, not through it.",
        "`proxy.ts` sets a per-request **CSP nonce** (no inline scripts or styles) and does no authentication. The `(client)/layout.tsx` is the real login check: it reads `/v1/me` and redirects to `/login` on 401.",
        "**Every request and response is validated** against zod schemas generated from the API's OpenAPI file, and the schemas are strict, so web and API must be deployed together.",
        "**Live data**: `EventSource` for the Research board and run progress, a streaming `fetch` for Ask answers, polling fallbacks, and `router.refresh()` on some pages. There is no Redux or SWR: server components fetch; client components use plain state.",
        "The staff console cookie holds the **raw Google ID token**; a staff session lasts as long as that token.",
    ]))
    h.append(callout("Web leftovers", "18 stub pages and 2 empty layouts only redirect to `/` and can be deleted; `lib/query.ts` is used only by a test; `next.config.ts` enables a `dev.tsx` page extension for a gallery that does not exist; `web/README.md` and `docs/frontend/*` still describe removed screens; `e2e/tenant.spec.ts` expects a nav, buttons and an `/audit` redirect the code no longer has.", "warn"))
    h.append(end_chapter())

    # ---------------------------------------------------------------- 17
    h.append(chapter("17", "The API: every kind of route",
                     "234 routes in total. The full list with who may call each is Appendix A."))
    h.append(table(["Group", "Routes", "Examples"],
        [["Probes", "2", "`/healthz`, `/readyz` (ready only when the database is at revision 0097)"],
         ["Auth, sessions, identity", "20", "`/v1/auth/login`, `/v1/sso/{slug}/start`, `/v1/sso/session`, password reset, invites"],
         ["Research jobs", "10", "`/v1/research/estimate`, `POST /v1/research`, `/v1/research/live/stream`, resume, stop"],
         ["Runs and spend caps", "19", "`POST /v1/runs`, `/v1/runs/{id}/events`, cap approve / reject / extend"],
         ["Ask, chat, model choice", "12", "`POST /v1/chat/ask`, `/v1/chat/threads`, `/v1/models/choices`"],
         ["Documents and uploads", "15", "`POST /v1/documents/uploads`, `.../complete`, `.../confirm-company`"],
         ["Mail, drive, connectors, CRM", "20", "`/v1/mail-and-drive/*`, connector OAuth, CRM mappings"],
         ["Accounts, workspace, library, views", "29", "`/v1/accounts`, `/v1/accounts/{d}/workspace`, `/sheets/{sheet}`, parity bundle"],
         ["Downloads and exports", "7", "`download-everything`, `/v1/downloads/{job}`, `POST /v1/exports`"],
         ["Signals, alerts, feedback", "13", "`/v1/signals/*`, subscriptions (read-only inbox; engine unwired)"],
         ["Team, settings, audit, usage, support", "25", "`/v1/users/invite`, `/v1/support/grants`, `/v1/me`, notifications, audit export"],
         ["Operator (staff)", "49", "`/operator/v1/console/*`, tenant suspend / reactivate, library publish"],
         ["Worker tasks", "13", "`/tasks/run-slice`, `/tasks/research-job`, `/tasks/sweep`, `/tasks/document-process`"]],
        [30, 10, 60]))
    h.append(fig(FM.fig_request(), "What happens to every request on its way to the database.", wide=True))
    h.append(h2("Sign-in, sessions and CSRF"))
    h.append(ul([
        "Customers sign in with company SSO (OpenID Connect or SAML) or a first-party password (Argon2id; invite-only, no self-signup). The session is a 256-bit random token in an `__Host-ai_session` cookie (HttpOnly, Secure, SameSite=Lax); the database stores only its SHA-256. Idle timeout 30 minutes, absolute 8 hours.",
        "**CSRF**: every unsafe request must carry `X-CSRF-Token`, an HMAC of the session id under a server key in Secret Manager.",
        "**MFA**: 27 routes demand a fresh MFA step-up. Local password login is refused for admins because no TOTP verifier is wired, so admins can only enter through SSO with MFA.",
        "**Staff** use a Google ID token, email allow-lists and MFA on every call, even reads. To touch a tenant's data a staff member also needs a **time-boxed support grant** that a customer admin created; every call is audited in both places.",
        "**Worker tasks** require a Google-signed OIDC token for the worker audience from one named service account, and carry ids only.",
    ]))
    h.append(h2("Other conventions"))
    h.append(ul([
        "Errors are `application/problem+json`. Messages other than `invalid token`, `forbidden` and `not found` are replaced by the status title, so internals never leak.",
        "Rate limits stack: Cloud Armor at the edge (300 s ban), sign-in buckets in Redis, per-user money and compute buckets (`config/rate_limits.yaml`, all marked \"proposal, unmeasured\"), monthly per-user counts, per-thread chat limits.",
        "Idempotency: `Idempotency-Key` on `POST /v1/runs`; `request_id` in the body on research, refresh, ask and download-everything.",
        "The server has exactly one HTTP middleware (request id, logging, metrics, security headers). No CORS layer exists, because the browser only talks to its own origin.",
        "API documentation pages (`/docs`, `/redoc`) are disabled in staging and production.",
    ]))
    h.append(h2("What calls the worker"))
    h.append(table(["Trigger", "When", "Target"],
        [["Cloud Tasks queue `runs`", "per slice (8 attempts, 10-300 s back-off)", "`/tasks/run-slice`"],
         ["Cloud Tasks queue `jobs`", "per research job, per download", "`/tasks/research-job`, `/tasks/download-everything`"],
         ["Cloud Tasks queue `documents`", "per upload (20 attempts)", "`/tasks/document-process`"],
         ["Scheduler `sweep`", "every 5 min", "`/tasks/sweep`"],
         ["Scheduler `schedules-tick`", "every 15 min", "start due scheduled refreshes"],
         ["Scheduler `notify-relay`, `metrics`", "every 5 min, every 2 min", "email outbox; metrics"],
         ["Scheduler `maildrive-sync`", "hourly at :05", "per-user mail and drive sync"],
         ["Scheduler `isolation-check`, `access-expiry`, `audit-verify`", "02:00, 03:30, 04:15 UTC", "nightly integrity checks"],
         ["Scheduler `retention`, `alert-digest`", "**paused**", "routes that do not exist"],
         ["Cloud Run job `spend-invariants`", "03:17 UTC", "credit-ledger consistency check"]],
        [34, 30, 36]))
    h.append(callout("API oddities", "73 customer routes have no caller in the web app (connectors, CRM, usage, audit, settings, subscriptions, saved views, much of the parity bundle). SAML sign-in through the proxy is probably blocked, because the proxy rejects a cross-site form POST (unverified). The api service also serves the `/tasks/*` routes and the worker serves every `/v1` route; as deployed that is safe only because of Cloud Run ingress and IAM settings.", "warn"))
    h.append(end_chapter())

    h.append(part("Part 4  Data, cloud and security", "The database and how it keeps customers apart, the Google Cloud setup, how releases happen, and the security posture."))

    # ---------------------------------------------------------------- 18
    h.append(chapter("18", "The database and customer isolation",
                     "PostgreSQL 16 holds everything. About 205 tables across 96 migrations. Only 5 tables have ORM models; the rest are used through hand-written SQL."))
    h.append(fig(FM.fig_er(), "Tables grouped by domain.", wide=True))
    h.append(table(["Domain", "Key tables and what they hold"],
        [["Identity", "`tenants`, `users`, `memberships`, `auth_sessions`, `idp_connections`, `local_identities`, `support_grants`, `tenant_model_credentials` (a customer's own AI key, KMS-encrypted)"],
         ["Research", "`accounts` (target companies), `research_jobs` (the request), `account_runs` (the live run and its state machine), `run_leases`, `pull_manifests`. An older `runs` table still exists beside `account_runs`."],
         ["Evidence", "`facts`, `fact_evidence`, `grade_events`, `sources`, `coverage_rows`, `lead_verifications`, `abm_sense_checks`, `abm_distill_outputs`"],
         ["Pipelines", "`job_postings` and classifications; `people`, profiles and posts; `tech_detections`, `tech_stack_items`; `link_archive`"],
         ["Outputs", "`deliverables`, `export_packages`, `export_verifications`, `view_rows`, `run_gate_results`, `leak_checks`, `download_jobs`"],
         ["Money", "`tool_calls` (insert-only ledger of every paid call), `credit_entries` (the one credit ledger), `model_reply_log` (cost and reply cache), `usage_events`, `plan_limits`"],
         ["Files and search", "`documents`, `document_parts`, `document_passages`, `document_tables`, `chunks` (pgvector, 16 hash partitions; 384-dimension bge-small plus a 1,536-dimension hashing vector)"],
         ["Ask", "`chat_threads`, `chat_turns` (answer, tokens, cost)"],
         ["Operations", "`audit_events` (hash chain), `notifications`, `notification_outbox`, `isolation_checks`, `eval_*`"],
         ["LangGraph", "`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, created by the library; protected by a policy on the thread-id prefix"]],
        [18, 82]))
    h.append(h2("How customers are kept apart"))
    h.append(ol([
        "Every tenant table has a non-null `tenant_id` and **FORCE ROW LEVEL SECURITY**, which applies even to the table owner. The policy compares `tenant_id` with `app_tenant()`, a function reading a setting that only exists inside the current transaction.",
        "The application connects as role `app_rw`, which owns nothing and cannot bypass the policies. If no tenant is set, every policy matches zero rows (**fail closed**).",
        "`tenant_txn(tenant)` is the only normal way to run a query; it sets `app.tenant_id`, `app.user_id` and `app.scope` for that transaction only.",
        "A **second layer** (migration 0063) makes 102 tables private to the user who owns them, so even inside one customer a user cannot see another user's accounts, files or chats unless the scope is system.",
        "Work that must cross customers (sweeper, schedule tick, sign-in lookups, concurrency governor, purge) goes through named **SECURITY DEFINER functions** that return ids only, owned by no-login roles. Each carries a \"reviewed:\" comment enforced by a lint.",
        "Ledgers are insert-only (no UPDATE or DELETE grant). Child tables use composite foreign keys `(tenant_id, parent_id)` so a row can never point at another customer's parent.",
        "Proof: 12 isolation test files, a nightly `isolation-check` that probes every tenant table and read route, and an audit hash chain verified nightly.",
    ]))
    h.append(callout("Where isolation could still fail", "The whole model rests on code always going through `tenant_txn`. Raw sessions and `untenanted_session()` exist for jobs, and system scope is a context variable any code can set. Foreign keys bypass row-level security. The purge roles (migrations 0096-0097) are recent and lightly exercised. All services share one broad `app_rw` role.", "warn"))
    h.append(h2("Other storage"))
    h.append(table(["Store", "Holds"],
        [["Cloud Storage data bucket", "Raw uploads under `tenants/<tenant>/files/...`, raw provider payloads (deleted after the retention days), quarantine (7 days), resumable-upload staging. Encrypted with a customer-managed key; versioned; public access blocked."],
         ["Cloud Storage audit archive", "A separate bucket with a 7-year retention policy. The lock that makes it immutable is **not yet applied**."],
         ["Redis (Memorystore, TLS)", "Rate-limit buckets, a provider-cache index. Nothing authoritative or private."],
         ["Postgres full-text and vectors", "`tsvector` columns with GIN indexes; `halfvec` columns with HNSW indexes per partition."]],
        [30, 70]))
    h.append(end_chapter())

    # ---------------------------------------------------------------- 19
    h.append(chapter("19", "The Google Cloud setup",
                     "Defined in Terraform under `infra/` (about 4,400 lines, 15 modules). Staging is live in project `abmgenerator`, region `us-central1`. Production is prepared but not applied."))
    h.append(table(["Module", "What it builds"],
        [["`project`, `iam`", "Enables APIs; 8 service accounts (api, worker, job, web, migrate, tasks, scheduler, deployer) with least-privilege roles; Workload Identity Federation for GitHub so CI needs no stored keys."],
         ["`network`", "A VPC, a private subnet for Cloud Run, deny-all ingress firewall, Cloud NAT with one reserved static outbound IP (give this to providers that allow-list)."],
         ["`secret_manager`", "A KMS key ring and customer-managed key; empty secret containers (values are added out of band)."],
         ["`cloud_sql`", "PostgreSQL 16, private IP only, encrypted-only connections, IAM authentication, customer-managed encryption, point-in-time recovery, backups (7 days staging, 30 prod), deletion protection."],
         ["`cache`", "Memorystore Redis 7.2 with TLS (BASIC tier on staging, HA on prod)."],
         ["`gcs`", "The data bucket and the audit-archive bucket."],
         ["`tasks`", "Three queues: `runs` (5 dispatches/s, 8 attempts), `documents` (2/s, 20 attempts), `jobs` (5/s, 5 attempts)."],
         ["`registry`", "Artifact Registry for images."],
         ["`identity_platform`", "Google Identity Platform for customer tokens."],
         ["`cloud_run`", "Services api, worker (with ClamAV sidecar, optional NVIDIA L4 GPU, a mounted bucket) and web. Startup probe `/readyz`, liveness `/healthz`."],
         ["`cloud_run_jobs`", "`migrate`, `spend-invariants`, `reingest-tables`. A job `ai-staging-purge` exists in staging but is **not in Terraform** (made by hand)."],
         ["`scheduler`", "The cron ticks listed in chapter 17."],
         ["`edge`", "Global HTTPS load balancer, managed certificate, TLS 1.2+, Cloud Armor with OWASP rules and rate-based bans."],
         ["`observability`", "Log buckets, uptime checks, about 8 hand-written and 26 generated alert policies, a dashboard. **Application-metric alerts are off by default.**"],
         ["`budget`", "A billing budget with alerts at 50, 90 and 100 percent."]],
        [20, 80]))
    h.append(h2("Secrets, by name only"))
    h.append(p("`checkpoint-database-url`, `migrator-database-url`, `app-rw-password`, `anthropic-api-key`, `moonshot-api-key`, `csrf-signing-key`, `operator-google-client-secret`, the five provider keys `p-search-api-key`, `p-scrape-api-key`, `p-signals-api-key` and `-token`, `p-people-api-key`, `p-org-api-key`, `smtp-password`, optional mail and drive OAuth secrets, the Redis CA, plus runtime-created per-tenant secrets named `tenant-*`. The values are never in Terraform, images or git."))
    h.append(h2("Staging versus production defaults"))
    h.append(table(["Setting", "Staging", "Production (prepared)"],
        [["api / web instances", "0 to 3", "1 to 10"],
         ["worker instances", "0 to 2", "1 to 8"],
         ["Cloud SQL", "zonal, 200 connections, tier set in a private tfvars file (unverified; example is 1 vCPU, 3.75 GB)", "regional, 400 connections"],
         ["Redis", "BASIC", "STANDARD_HA"],
         ["`RESEARCH_CAPS`", "**off** (default, owner decision)", "on"]],
        [30, 40, 30]))
    h.append(h2("Local and fake modes"))
    h.append(ul([
        "`compose.yml` is a **hermetic** stack: Postgres, a fake storage server, a Cloud Tasks emulator, Redis, migrate, worker, a fake identity provider and the web. No keys needed. The fake model returns scripted answers; the fake providers replay recorded files.",
        "`compose.live.yml` adds real keys and costs real money. `compose.replica.yml` is a separate stack on other ports for the replica screens.",
        "The cloud profiles refuse every local switch (`LLM_FAKE`, `ALLOW_UNAUTHENTICATED_TASKS`, hermetic providers), so a fake cannot reach production.",
    ]))
    h.append(end_chapter())

    # ---------------------------------------------------------------- 20
    h.append(chapter("20", "Releases, checks and daily operations",
                     "What the repository promises, and what the owner actually seems to do."))
    h.append(fig(FM.fig_release(), "How a change reaches the cloud, as practised.", wide=True))
    h.append(table(["Tool", "What it is for"],
        [["`make check`", "The full pre-pull-request gate on a scratch database: lint, types, import contracts, comment and guarantee checks, loop caps, route catalogue, migrations, RLS catalogue SQL."],
         ["`make test-isolation`", "The row-level-security isolation suite, a hard gate."],
         ["`make contract`", "Regenerates and checks the OpenAPI file, the generated web client, the route catalogue and the wiring inventory."],
         ["`make up` / `make replica-up`", "Starts the hermetic local stacks."],
         ["GitHub Actions `ci.yml`", "Lint, quality, infra tests, web tests and build, e2e, dependency audit, secret scan, image build, hermetic end-to-end run."],
         ["`deploy-staging.yml`, `deploy-prod.yml`", "Sign and push images, run the migrate job, deploy by digest, smoke test, roll back on failure. **Whether these have ever run green is unverified**; the notes say real releases use Cloud Build and gcloud."]],
        [30, 70]))
    h.append(ul([
        "**Release order**: build api and worker on Cloud Build, run `make release-check` (Trivy scan, image checks), run the migrate job, deploy worker then api then web by digest, smoke test `/readyz`, update the digests in Terraform variables. Roll back by sending traffic to the previous revision.",
        "**After a migration**, bump `EXPECTED_REVISION` in `api/routes/health.py`, or `/readyz` refuses to serve.",
        "**Terraform** is applied by hand from a saved plan. One stale plan once rolled the api and worker back.",
        "**Observability**: structured logs with request ids, `llm.usage` per model call, a stall dump of every thread if the loop freezes 15 s, and about 34 alert policies. The place to look when a run fails is the **LangGraph checkpoint tasks**, where the real error is stored.",
        "The nightly integrity checks (`isolation-check`, `audit-verify`, `access-expiry`, `spend-invariants`) are the only automatic safety net; the data-retention sweeper is paused.",
    ]))
    h.append(end_chapter())

    # ---------------------------------------------------------------- 21
    h.append(chapter("21", "Security posture",
                     "Strong design, a few real gaps. Severity is my judgement."))
    h.append(h2("Controls that are present"))
    h.append(table(["Area", "Implementation"],
        [["Authorization", "Default-deny route catalogue (219 of 219 routes covered) with roles, principal types, audit class; staff plane separate."],
         ["Sessions and CSRF", "Hashed 256-bit tokens in `__Host-` cookies, 30 min idle, 8 h absolute, HMAC CSRF, MFA step-up."],
         ["Isolation", "Forced row-level security plus a per-user layer, definer functions, nightly probe."],
         ["Web edge", "Nonce CSP with no inline styles or scripts, HSTS, frame-ancestors none, sanitised Markdown, Cloud Armor OWASP rules."],
         ["Uploads", "Direct-to-storage upload, ClamAV fail-closed, content-based type detection, sandboxed parsers, archive-bomb guards."],
         ["Outbound calls", "SSRF validator: https only, port 443, no internal or metadata addresses, every resolved address must be public."],
         ["AI safety", "Strict schemas, canary strings, verbatim-quote checks, deny-list scan of every output, downgrade-only judges."],
         ["Secrets and keys", "Secret Manager, customer-managed encryption keys, KMS envelope encryption of customers' own AI keys, gitleaks in CI."],
         ["Audit", "Per-tenant hash-chained insert-only log, verified nightly, with an archive bucket."],
         ["Supply chain", "Digest-pinned base images, Actions pinned to SHAs, cosign signing, Trivy gate, `npm audit`."]],
        [22, 78]))
    h.append(h2("Weaknesses"))
    h.append(table(["Severity", "Finding"],
        [[sev("High"), "**The `security` package is a stub that always says all clear** (`beta_gate()` returns True, `rotation.overdue()` returns empty, `soc2.coverage()` returns 1.0). Tests pass against it. Anyone reading a green \"security gate\" is misled. `metrics`, `registers` and `unitcost` are also no-ops."],
         [sev("High"), "**Real company data in git history**: `unwanted-things/real-account-fixtures/.../GSK_workspace.zip` is tracked, and the repository has a GitHub remote (`go6th/ABM`). Whether it is private is unverified. Removing it needs a history rewrite."],
         [sev("Medium"), "The Python **sandbox** (isolated subprocess, resource limits, audit hook) is not a real security boundary; native libraries parsing hostile files and hook-removal tricks can escape it. The parser child shares the worker container's filesystem and network namespace."],
         [sev("Medium"), "One broad `app_rw` role serves api and worker; isolation depends on code always using `tenant_txn`."],
         [sev("Medium"), "`CHECKPOINT_DATABASE_URL` is a password-bearing URL even in the cloud; secret rotation is manual."],
         [sev("Medium"), "Cloud Run `allUsers` invoker on api and web is safe only while ingress settings hold. No IAP or mTLS between web and api."],
         [sev("Medium"), "Session, MFA and rate-limit values are all marked \"proposal\" and unmeasured. No local MFA; admins must use SSO."],
         [sev("Medium"), "Redis TLS does not check the hostname (CA pinned instead); sign-in is refused if Redis is down."],
         [sev("Low"), "Staging has every spending cap off; the audit archive lock is not applied; the retention sweeper is paused; application-metric alerts are off."],
         [sev("Low"), "`compose.yml` publishes Postgres, Redis and others on all interfaces with fixed dev passwords; fine on a laptop, risky on a shared network."],
         [sev("Low"), "Both Python images include LibreOffice, Node and poppler, a large attack surface; `apt-get upgrade` makes builds non-reproducible."],
         [sev("Info"), "Ask can call tools (including optional Python) driven by text from uploaded files, a prompt-injection surface that is mitigated by the sandbox and a per-tenant flag."]],
        [11, 89]))
    h.append(end_chapter())
    return "\n".join(h)
