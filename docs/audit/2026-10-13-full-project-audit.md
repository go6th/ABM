# Full project audit: what every part is, how it works, and what state it is in

Date: 2026-10-13. Branch `feat/research-files` (all of today's work is uncommitted on it).
Method: read the README, `docs/product-overview.md`, every backend package's module header, every
web page, the config files, the migrations, the Makefile and infrastructure folders; measured the
live dev database and stack. Numbers below are measured unless marked otherwise.

---

## 1. What the project is

**Account Intelligence** (product name "6th agents") is a multi-tenant SaaS for B2B sales teams.
A user names one company (its web domain). The platform researches it and returns:

1. a **sourced, graded account brief**: every fact carries the link that proves it, and a grade
   (Confirmed, Probable, Hypothesis, Lead) computed by code, never by a model;
2. a **senior people list** (Director and above), deduplicated;
3. a **coverage tracker**: 33 research areas, each Done, Partial, Not done, Blocked or None, with
   proof of what was searched and why anything is missing;
4. **files** (Word summary, Excel tables, a workspace zip) and later **refresh and alerts**.

Only Confirmed facts may be said to a prospect; everything else becomes a question. The system
never sends outreach. Many client companies (tenants) share one installation; the database itself
keeps their data apart.

It started as a one-person research skill in claude.ai (13 manual steps per account). This
repository turns that skill into a product.

### Size

| Part | Measure |
| --- | --- |
| Backend, Python | 84,107 lines in 51 packages |
| Frontend, TypeScript | 11,395 lines, 37 pages |
| API | 140 operations (114 client, 26 operator) |
| Database | 198 tables, 51 migrations |
| Configuration | 66 YAML files |
| Tests | 328 test files; last full run 3,702 passed |

---

## 2. How it fits together

```
Browser
  │  HTTPS, session cookie
  ▼
Web app: Next.js 16 (web/, port 3000)
  │  every /v1 call is forwarded by web/app/v1/[[...path]]/route.ts (same-origin check,
  │  only the session cookie and safe headers forwarded); per-request CSP in web/proxy.ts
  ▼
API: FastAPI (src/account_intelligence/api, port 8080)
  │  route guard: every route must be in the catalog (api/policy.py) with roles and an audit
  │  class; tenant comes ONLY from the verified identity; CSRF on cookie sessions
  ├──> PostgreSQL 16 + pgvector (port 5432): all data, forced row-level security
  ├──> Redis 7 (port 6379): rate limits, research cache index
  └──> task queue (Cloud Tasks; local: HTTP to the worker)
          ▼
Worker: same image, different role (port 8081)
  │  runs research in time slices (LangGraph, checkpoint in Postgres after every step)
  ├──> data providers (search, scrape, signals, org, people), through a paid-call ledger
  ├──> Claude models through the model router (config/models.yaml)
  └──> object storage (raw pages, uploaded files, built files): local volume or Cloud Storage
```

Local development runs all of this in Docker Compose (`make up`): db, migrate, api, worker, web,
a fake identity provider, Cloud Storage and Cloud Tasks emulators, and Redis. `compose.live.yml`
adds real provider and model keys from `.env` (costs money). Production targets Google Cloud:
Cloud Run (api, web, worker), Cloud Run Jobs, Cloud SQL, Cloud Storage, Memorystore, Secret
Manager, Cloud Tasks (Terraform in `infra/`).

---

## 3. The backend, part by part

Grouped by job. "State" says whether it works today, end to end, on the local stack.

### 3.1 The research run (the core product)

| Package | What it is for | How it works | State |
| --- | --- | --- | --- |
| `account_intelligence/runs` | Run lifecycle | Creates the run row, reserves wallet credits and seeds 33 tracker rows in one transaction; enforces the state machine (QUEUED, RUNNING, AWAITING_APPROVAL, DISTILLING, VERIFYING, then COMPLETED, PARTIAL, BUDGET_EXCEEDED, FAILED or CANCELED); settles money exactly once. Today: `stop.py` (redacted stop detail, PARTIAL vs FAILED rule), `sweeper.py` (orphaned runs end `worker_lost`). | Works |
| `account_intelligence/workflow` | Workflow v1: the 13-stage graph | `config/workflow_v1.yaml` defines the stages; `engine.py` builds a LangGraph graph; `runner.py` runs one time slice, checkpoints, cancels within seconds, pauses for approval, and ends the run through one terminal handler. `deps.py` is the only place model calls go through (with the replay and reply cache). | Works; the default path for "full" runs unless the tenant has the `abm_agents` flag |
| `agents` | The ABM agent layer: the original skill's 8 steps as bounded agents | `graph.py`: setup, cap approval, pull (code only, no model), interpret (extraction by model, grading by code), sense check, targeted pulls, distill, build, gate, deliver. Every agent has a contract (`config/agents.yaml`): allowed tools, schemas, token and time budgets, guards G1 to G17. | Works up to build; see finding F1 |
| `account_intelligence/connectors` | Paid data providers | `call_operation` is the only entry: validate arguments, URL policy, budget reservation and a `started` row in one transaction, circuit breaker, rate limiter, the HTTP call, store the raw body, parse, record cost. A finished call replays free (pull once); today also across runs (research cache). Providers by neutral code: p_search, p_scrape, p_signals, p_org, p_people. | Works (live keys in `.env`) |
| `parallel` | Running research units side by side | A dependency graph of units with per-provider concurrency slots (Postgres leases) and single-flight for identical calls. | Built; off by default (`execution_mode`) |
| `account_intelligence/grading` | The grade of every fact | Pure rules: source tiers, freshness half-lives, primary-source reading, Lead verification (three phrasings across two tools), noisy-OR confidence, the A to D engine grade mapped to product grades; one write path for facts with lineage. | Works |
| `account_intelligence/tracker` | The 33-row coverage tracker | Statuses are derived from ledger evidence (never typed by a model); proof is cumulative; the completion rule decides COMPLETED vs PARTIAL. | Works |
| `verify` | Blind re-reading of claims | A second model re-reads the stored source without seeing the claim's grade; it may only downgrade. | Built |
| `jobs` | Job postings (step 5) | Pulls boards per platform lane and 12 months of postings, classifies each posting (build, run, or unrelated) with evidence sentences. | Built |
| `people` | People list (step 7) | Title search per lane and seniority, merge by profile URL then name, scope rules, priority levels; profile enrichment needs a provider grid that is not set up. | Partial (profiles NotConfigured) |
| `gate` | Quality gate before delivery | Fail-closed checks on the built files. | Core built; not wired (finding F1) |
| `exports` | The deliverable files | Builds Word and Excel from one stored snapshot, scans every file for leaks (provider names, credit counts), stores and records them. Local renderer `python-interim`; cloud default `node` renderer is NotConfigured. | Partial (finding F2) |
| `account_intelligence/agents` | Shared prompt and parsing pieces | Versioned prompts, JSON extraction from model output, grading facade. | Works |

### 3.2 Models and cost

| Package | What it is for | State |
| --- | --- | --- |
| `config/models.yaml`, `platform/llm.py` | Which model serves each task. Arm `cost_saving` (today): extraction and classification on Sonnet 5.5, planning and verification on Opus 5.5, synthesis and narrative on Fable 5.1; effort level and a cached system prompt per task. | Works; no model key in the local stack right now |
| `llm` | The model router with candidate ladders, fallback, health and call records (`MODEL_ROUTER=on`), plus `reply_cache.py` (today: reply log and reuse) and `tasks.py` (every call names a registered task kind). | Router built but off (needs a benchmark, decision D-51); legacy port path in use |
| `account_intelligence/model_routing.py` | Phase policies, allowlist, pricing, quality checks. | Works |
| `spend`, `metering`, `governance`, `unitcost` | Credit caps per run, wallet ledger, usage events, plan limits, monitoring budget, cost per unit. | Works |
| `connectors/cache.py` (today) | Reuse of finished provider calls across runs of one tenant, within a TTL; Redis index. | Works |

### 3.3 Client features beyond the run

| Package | What it is for | State |
| --- | --- | --- |
| `account_intelligence/chat`, `rag` | Chat about an account, grounded in its stored facts, with citations; `rag` is a fuller retrieval loop (pgvector). | Chat works with a model key; `rag` built, not wired |
| `views` | The five client tables (people, jobs, tech, facts, coverage), saved views, safe rendering. | Works |
| `refresh`, `signals`, `alerts` | Shelf life per signal, scheduled refresh, change detection, alert rules, digest. | Engines built; Slack and email channels NotConfigured |
| `crm_writeback` | Create-only notes and tasks in Salesforce or HubSpot. | Logic built; CRM adapters NotConfigured |
| `connectors_client` | The client's own Gmail and Drive (narrow scopes, quarantine). | Logic built; OAuth apps not registered |
| `opendoor` | A signal event contract and webhooks for a later outreach product. | Built (read side only) |
| `research_files` (today) | A user's own file of any type and size: resumable upload, audit, analytics, cited questions. | Works (section 9) |

### 3.4 Security, tenancy and operations

| Package | What it is for | State |
| --- | --- | --- |
| `identity`, `api/auth.py` | Sign-in: local password (Argon2id), OIDC and SAML broker, server-side sessions, CSRF, step-up MFA for admins, rate limits. | Local password and the fake IdP work; real IdPs not connected |
| `tenancy` | Tenant settings (lanes, people scope, exclusions), the registry of every tenant-scoped object. | Works |
| `audit` | Insert-only, hash-chained audit of reads, runs, exports and admin actions. | Works |
| `admin`, `api/routes/operator_*` | Operator console: tenants, quotas, flags, support access, runs, failures, spend. | Works; staff sign-in needs Google Workspace |
| `privacy`, `datamodel`, `contracts`, `quality`, `crosscut`, `registers` | Retention, DSAR, deletion, data model spec, drift checks, conformance as data. | Built |
| `obs`, `metrics` | Structured logs (allow-listed fields, no prompts), traces, SLOs, stuck-run scan, canary probes. | Built |
| `evalgate`, `pilot` | Eval harness with golden sets; pilot feedback and usefulness metric. | Built; golden labels not yet real |
| `account_intelligence/platform`, `app` | Ports and adapters (hermetic for tests, Google Cloud for staging and production); the container is the only place adapters are chosen. | Works |

---

## 4. The frontend, page by page

All pages are in `web/app`. Data comes from the generated, typed client (`web/lib/gen/api.gen.ts`,
built from the OpenAPI snapshot), so a backend contract change breaks the web build instead of
failing silently.

| Area | Pages | What the user does |
| --- | --- | --- |
| Sign in | login, forgot, reset, verify-email | Email and password, or SSO |
| Home | `/` | One question box across accounts; up to five recent changes |
| Accounts | `/accounts`, `/accounts/[domain]`, history, tables | Add an account, read its graded facts, open the five tables, start "Refresh from web" (a run) |
| Research runs | `/runs`, `/runs/[run_id]` | Watch progress, approve the spend cap, cancel; today: why it stopped, what it kept, what is missing, Resume |
| Research files (today) | `/research-files`, `/research-files/[file_id]` | Upload any file (resumable), read its audit and analytics, ask questions, approve cost, resume, delete |
| Conversations | `/chat` | Chat grounded in an account's facts |
| Alerts, Files, Usage, Feedback, Audit | `/alerts`, `/downloads`, `/usage`, `/feedback`, `/audit` | Read alerts, download built files, see usage, give feedback, read the audit log |
| Settings | users, sso, security, connectors, crm, alerts, support, model-key | Workspace administration |
| Operator console | `/operator/...` (tenants, runs, failures, spend, cost, configuration, support) | 6th staff only |

Design system in `web/ui` (tokens, light and dark themes, primitives). Security: a nonce-based
Content-Security-Policy per request, same-origin checks on every write, no inline styles.

---

## 5. Data and storage

* **PostgreSQL**: 198 tables. 155 have row-level security ENABLE and FORCE with a policy scoped
  to the application role. The 16 `chunks_p*` partitions have no own policy, but the application
  role has no privilege on them (checked): every read goes through the parent, which is forced.
  The other 27 tables without RLS hold platform data with no `tenant_id` (migrations, flags,
  provider limits, SLO snapshots, release checks). No tenant table was found without RLS.
* **Cross-tenant jobs** (spend invariants, orphan sweeps) get identifiers only through narrow,
  reviewed `SECURITY DEFINER` functions, then read each tenant under its own context.
* **LangGraph checkpoints** live in Postgres under a tenant-prefix policy.
* **Object storage**: raw provider payloads (content-addressed, neutral keys), built files,
  uploaded research files.
* **Redis**: rate-limit buckets and cooldowns, the research cache index (never content).

---

## 6. Configuration

66 YAML files in `config/`, each value with a `source` or a `status: proposal`. The important ones:

| File | Controls |
| --- | --- |
| `checklist.yaml` | The 33 coverage rows |
| `workflow_v1.yaml`, `agents.yaml`, `skill/*` | The run's stages, agents and the skill content |
| `providers.yaml`, `routing.yaml`, `source_registry.yaml` | Providers, their routes and fallbacks, source tiers |
| `grading.yaml`, `grading_policy.yaml`, `tracker_v1.yaml` | Grading and completion rules |
| `models.yaml`, `model_routing.yaml` | Model per task, prices, effort, token limits |
| `spend.yaml`, `billing.yaml`, `plans.yaml` | Caps, settlement, plan limits |
| `research_cache.yaml`, `research_files.yaml` | Today's cache and file features |
| `roles.yaml`, `session.yaml`, `auth.yaml` | Roles, sessions, password policy |
| `retention.yaml`, `deletion.yaml`, `dsar.yaml` | Privacy |

Many values are proposals waiting for the product owner (decisions D-01 to D-102).

---

## 7. Tooling, tests and gates

* `make` targets: `up`, `test` (isolation suite first), `check`, `static` (import contracts,
  docstrings 100%, headers, guarantee tags, bounded loops), `contract` (OpenAPI and generated
  client), `scan` (leaks), `verify`, `infra-test`, `smoke`, `doctor`.
* **Hard gate**: `tests/isolation` (413 tests today) plants rows for tenant A and probes every
  table and every route as tenant B.
* Every module declares `GUARANTEE(test_name)` tags; a script fails if a tag names no test.
* Web: TypeScript strict, ESLint, Vitest (96 tests), Playwright suites.

---

## 8. Findings

### What is strong

* Tenant isolation is enforced by the database and tested on every table and route.
* Money is a ledger: paid calls recorded before they are made, replayed instead of paid twice,
  one settlement per run.
* Grades are computed by code; models extract, they never decide confidence.
* Durability: checkpoints after every step; after today, every stop keeps its data and can resume.
* The engineering discipline is unusually high (route catalog, guarantee tags, bounded loops,
  100% docstrings, isolation matrix).

### Problems, most important first

| # | Finding | Effect | What would fix it |
| --- | --- | --- | --- |
| F1 | **No quality gate is wired** in the dev or cloud container (`Container.gate` is never set). The ABM graph then reports `gate_not_configured`; Workflow v1 skips the gate. | No run can end COMPLETED. Measured: 0 COMPLETED runs of 28 in the database. | Wire `gate` in `app/container.py` with the ported checks |
| F2 | **Files are not built in the cloud**: the cloud default renderer (`node`) is NotConfigured; locally the `python-interim` renderer refuses a Summary without a narrative. | Runs end PARTIAL `build_failed`; clients get tables but no Word file | Port the renderer; distill must write the Summary sections (`report_sections`, open item OI-EXP-1) |
| F3 | **The research spent money without a deliverable**: 1,902 paid provider calls, 221.64 USD, on 2026-10-11 and 2026-10-12; two in three were repeats. | Fixed for the future today (cache, resume, durability); spent money is not recoverable | Done; verify with one live run once a model key with credits exists |
| F4 | **No model key** in the local stack now (the worker's `ANTHROPIC_API_KEY` is empty). | Runs stop at the first model step; file summaries are extracts; answers are extractive | Set a key with credits in `.env`, then `docker compose restart api worker` |
| F5 | **The README is wrong in places**: it names "Claude 3.5 Sonnet" (not used), a demo login `doctor@synthetic.local` that does not exist, "20 frontend tests" (96), and chat thread names that are not real data. `docs/STATUS.md` (2026-10-09) calls everything "Completed", which F1 and F2 contradict. | Misleads anyone new to the project | Rewrite both from the measured state |
| F6 | **Seven tests fail on a clean HEAD** (a vendor-name scan flags a committed test file; two fake-model judge tests; four wiring tests import `REQUEST_ID` and `components`, which the source never defined). | The suite is never fully green; real regressions hide among known failures | Fix the four imports, the fake judge and the scan |
| F7 | **Several adapters are NotConfigured in production**: people profiles, Slack and email alerts, CRM write-back, Gmail and Drive OAuth apps, a provider balance endpoint, a malware scanner, OCR, speech transcription, real identity providers. | Those features exist as logic but do nothing for a client | Configure one at a time, each behind its test |
| F8 | **Open decisions**: 115 rows in `docs/open-items.md`; D-01 (grade mapping), D-51 (router benchmark), D-04 (prices) and others wait for the product owner. | Values marked "proposal" are live defaults | Owner review |
| F9 | **Nothing is deployed**: Terraform and deploy scripts exist; no cloud project, region or domain is set (`docs/STATUS.md` section 3). | Local only | Owner provides the project and domain |
| F10 | **Uncommitted work**: three passes today (research cache, durable runs, research files) sit uncommitted on `feat/research-files`; the last full test run with every fix was stopped before it finished. | Risk of loss; not yet confirmed green | Rerun the suite, then commit |
| F11 | **The model router is off** (`MODEL_ROUTER` off, `benchmark_report` null) and `config/model_routing.yaml` still lists an old Haiku id with no prices. | Fallback, health and per-call records of the router are not used | Run the benchmark (D-51) or remove the stale candidate |

---

## 9. Today's additions in context

| Work | What it fixed | Where |
| --- | --- | --- |
| Research cache | Repeat provider calls across runs; model replies reused | `connectors/cache.py`, `llm/reply_cache.py`, migration 0049 |
| Cost-saving routing | Cheaper model for extraction, effort per task, cached prompts | `config/models.yaml`, `platform/llm.py` |
| Run failure handling | Credits out, missing key, refused build, prompt too long, stale in-flight call no longer FAILED with the money lost | `agents/graph.py`, `workflow/runner.py`, `connectors/ledger.py` |
| Durable runs | Write-ahead for provider and model calls, one terminal handler, orphan sweeper, resume, partial output in API and UI | `runs/stop.py`, `runs/sweeper.py`, migration 0050, run page |
| Research files | Bring any file; audit by code; analytics with sources; cited questions; cost gate; resume; delete | `src/research_files`, migration 0051, `/research-files` pages |

Measured on the research files feature: a 1.10 GB CSV (1,828,386 rows) uploaded in 8 MiB parts and
processed with the same peak memory as a 20 MB file (133.3 MB); re-upload deduplicated at $0.

## 10. Recommended order of work

1. Rerun the full suite and commit today's work (F10).
2. Wire the quality gate and finish the Summary narrative so a run can end COMPLETED (F1, F2).
3. Fix the seven known failing tests so the suite can be green (F6).
4. Correct the README and the status page (F5).
5. With a funded model key, run one account end to end and measure cost and time (F3, F4).
6. Then the owner decisions and the remaining adapters (F7, F8), then deployment (F9).
