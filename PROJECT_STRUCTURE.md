# Project structure

This is the map of the whole repository: every folder, what it is for, who uses it, and how
the pieces connect. Each top-level folder also has its own `README.md` with the detail for that
folder. Read this file first; it was written on 2026-10-16 after a full audit (what the audit
moved out of this folder is listed in `/home/sakib/unused code ABM generator/README.md`).

## 1. What this project is

Account Intelligence (the "ABM Generator") is a multi-tenant web application. A client signs
in, names a company, and the platform researches it from public sources and from the client's
own uploaded files. It returns a sourced, graded account brief (Word), the people, job postings,
technology and news sheets (Excel), an answer box ("Ask") over everything it found, scheduled
refreshes, and a 6th admin console for operators. Every client is a sealed tenant: isolation is
enforced by PostgreSQL row-level security, never by application code.

One code base, three deployable services built from two images:

| Service | Image | Entry point | What it does |
| --- | --- | --- | --- |
| `web` | `web/Dockerfile` | `node server.js` (Next.js standalone) | The screens. The browser talks only to this origin; `/v1/*` is proxied to the API server-side so the session cookie stays first-party. |
| `api` | `docker/api.Dockerfile` | `uvicorn account_intelligence.api.main:app` | FastAPI. Authenticates, enforces the route catalog, reads and writes the tenant's data under forced RLS, enqueues background work. |
| `worker` | `docker/worker.Dockerfile` (same code, `SERVICE_ROLE=worker`) | same ASGI app, serving `/tasks/*` | Executes the background work that Cloud Tasks and Cloud Scheduler post to it: research runs, document parsing and indexing, scheduled ticks. Also the CLI `python -m account_intelligence.worker` for the nightly checks. |

Plus a one-shot migration job (`alembic upgrade head`, the api image) that runs before every
release.

## 2. The folders

```
ABM Generator/
├── README.md                 Start here: what it is, how to run it, where everything is.
├── PROJECT_STRUCTURE.md      This file.
├── BUILD_LOG.md              The running build log (every work package, decision and gate result).
├── CONTRIBUTING.md           Branch names, commits, the twelve rules that must never be broken.
├── DEPLOYMENT.md             How the local stack and the Google Cloud environments are run.
├── RUNBOOK.md                Operator and release runbook (readiness, rollout, incidents).
├── SECURITY.md               How to report a vulnerability; the security posture in short.
├── NOTICE                    Third-party notices.
├── Makefile                  Every developer command (`make help`): up, test, lint, static, build…
├── pyproject.toml            Python project: dependencies, ruff/mypy/pytest/coverage settings,
│                             import-layer contracts (import-linter), docstring and mutation gates.
├── uv.lock                   Locked Python dependency set (installed with `uv sync --frozen`).
├── alembic.ini               Alembic entry (migrations/env.py does the work).
├── compose.yml               Local stack: db, redis, emulators, migrate, api, worker, web, fake IdP.
├── compose.live.yml          Opt-in override that gives the local api/worker the real keys from .env.
├── compose.replica.yml       The same application as a second, hermetic demo stack (fake model,
│                             synthetic providers, own ports and database; nothing paid can happen).
├── .env.example              Every environment variable the code reads, documented (test-enforced).
├── .env                      Your local secrets (git-ignored, never committed).
├── .github/workflows/        CI (ci.yml), nightly evals (eval.yml), deploys (_deploy, staging, prod).
├── .claude/skills/           The two Claude skills used to build and consolidate this platform.
├── .pre-commit-config.yaml   ruff, whitespace, gitleaks, migration isolation lint.
├── .gitleaks.toml            Secret-scan allowlist (reviewed paths only).
│
├── src/                      ALL application code (Python). See src/README.md.
│   ├── account_intelligence/   The application: api, app wiring, db, platform ports, connectors,
│   │                           grading, tracker, workflow, runs, worker, replica (the product screens),
│   │                           chat, rag, safety.
│   ├── agents/                 The ABM research graph (LangGraph): the eight-step run.
│   ├── exports/                Word, Excel and zip builders (the deliverable files).
│   ├── documents/, research_files/, devstore/   Uploaded files: upload, scan, parse, index.
│   ├── jobs/, people/, signals/, refresh/, alerts/   Pipelines and the refresh/alert engine.
│   ├── spend/, metering/, governance/, unitcost/   Money: credit cap, usage, budgets, unit cost.
│   ├── identity/, tenancy/, admin/, audit/, views/, connectors_client/, pilot/, crm_writeback/,
│   │   opendoor/                Tenant plane, operator plane, audit, tables, client connectors.
│   ├── llm/, verify/, evalgate/, parallel/, obs/   Model routing, verifiers, evals, scheduler, telemetry.
│   └── crosscut/, datamodel/, contracts/, quality/, privacy/, security/, metrics/, registers/
│                               Roadmap section 11 cross-cutting checks (conformance as code).
├── config/                   Every rule the code reads as data (YAML): grading, agents, budgets,
│                             telemetry, the owner's research skill archive… See config/README.md.
├── migrations/               Database schema: numbered SQL files + Alembic wrappers. See its README.
├── tests/                    The test suite (4,000+ tests), one folder per subsystem. See its README.
├── scripts/                  Generators and gates run by the Makefile and CI. See scripts/README.md.
├── web/                      The Next.js app (client workspace, auth, operator console). See web/README.md.
├── infra/                    Terraform for Google Cloud: modules, the stack, staging and prod roots.
├── docker/                   The api and worker Dockerfiles and the CI image/stack scripts.
├── ops/                      Local fake IdP, load and restore drills, status page generator.
├── evals/                    Golden accounts and offline benchmark harnesses (nightly eval gate).
├── unwanted-things/          Parked files that are not the product (moved, never deleted): its
│                             README lists each one and why; cleanup.sh holds the irreversible steps.
└── docs/                     Living documentation: architecture, decisions (ADRs), runbooks,
                              provider contracts, security, privacy, the generated reference docs.
```

Not tracked, regenerable: `.venv/` (uv), `web/node_modules/`, `web/.next/`, the `.mypy_cache`,
`.ruff_cache`, `.pytest_cache`, `.hypothesis`, `.import_linter_cache` folders, `infra/**/.terraform/`.
`make clean` removes the caches.

Outside this folder: `/home/sakib/unused code ABM generator/` holds what the audit removed from the
code base (inventory and clean-up script inside), and the staging secrets live in Secret Manager.

## 3. How the pieces connect

### A request from the browser

```
browser ──HTTPS──▶ web (Next.js)                       web/app/**  web/proxy.ts (CSP nonce)
                     │  /v1/*  proxied with the session cookie     web/app/v1/[[...path]]/route.ts
                     ▼
                   api (FastAPI)                       src/account_intelligence/api/main.py
                     ├─ route catalog, default deny     api/policy.py, api/guard.py
                     ├─ who is calling                  api/auth.py, identity/*, api/membership.py
                     ├─ tenant transaction (forced RLS) db/session.py: tenant_txn()
                     ├─ PostgreSQL + pgvector           Cloud SQL (cloud) / compose db (local)
                     ├─ Redis                           rate limits, research-cache index
                     ├─ object storage                  payloads, uploads (GCS / devstore locally)
                     └─ enqueue background work         platform/gcp/adapters.py → Cloud Tasks
```

The web app never holds a secret: the API origin is decided server-side (`web/lib/api-origin.ts`),
Server Components call the API with `web/lib/server-api.ts`, the browser with `web/lib/api.ts`.
The operator console signs in with Google OIDC through its own BFF routes
(`web/app/api/operator/*`) and talks to `/operator/v1/*`.

### A research run ("Research a new company", Refresh, a schedule)

```
POST /v1/research or /v1/runs            api/routes/research.py, runs.py, replica/jobs.py
  → runs.lifecycle.create_run            one transaction: run row + credit reservation
  → Cloud Tasks queue "runs"             task = ids only
  → worker POST /tasks/run-slice         api/routes/tasks.py
      → workflow/runner.py               slices, checkpoints, retries, the finish rule
          → agents/graph.py              setup → cap_approval → pull → interpret → sense_check
                                         → targeted_pulls → distill → build → gate → deliver
              connectors/*               provider calls, each paid call recorded once (ledger)
              grading/*                  facts + evidence → engine grade → product grade
              tracker/*                  the 33 coverage rows, proof-backed
              exports/*                  Word, Excel, zip from one snapshot; safety gate
  → payload store                        the deliverables (GET /v1/exports, /v1/downloads)
  → report_sections, facts…              what the Account workspace and the sheets show
```

The LangGraph checkpoint (PostgreSQL) is the source of truth; a worker that dies mid-slice is
resumed by Cloud Tasks from the checkpoint. The model is reached only through `llm/` (routing)
and `agents/runtime.py` (budget reservation, output guards, the owner's skill as system context
from `agents/skill_context.py`).

### An uploaded file

```
browser ──resumable upload──▶ object storage directly   web/lib/upload.ts (devstore locally, GCS in cloud)
POST /v1/documents/*                                    api/routes/documents.py → documents/service.py
  → quarantine scan (clamd sidecar)      → Cloud Tasks queue "documents"
  → worker POST /tasks/document-process  → parse in a sandbox (research_files/parsers), redact,
                                           chunk, embed (fastembed) in ten-minute slices, index for
                                           Ask and file research; a Job Postings, People List or Tech
                                           Stack workbook also becomes the account's rows
                                           (documents/deliverables.py: the deep-dive counts and sheets)
```

### Scheduled and background work (Cloud Scheduler → worker)

| Tick | Path | Every |
| --- | --- | --- |
| schedules-tick | `/tasks/schedules-tick` | 15 min: start due refreshes |
| sweep | `/tasks/sweep` | 5 min: ends runs whose worker died, marks stalled runs, continues both automatically (`replica/auto_resume.py`), closes ended jobs, re-queues orphaned documents |
| notify-relay | `/tasks/notify-relay` | 5 min: run notifications (email when configured) |
| metrics | `/tasks/metrics` | 2 min: app metrics → Cloud Monitoring |
| maildrive-sync | `/tasks/maildrive-sync` | hourly |
| isolation-check, access-expiry, audit-verify | nightly | tenant isolation probe, idle access, audit chain |
| retention | `/tasks/retention` | nightly, PAUSED until the retention policy is signed off |
| spend-invariants | Cloud Run Job `python -m account_intelligence.worker spend-invariants` | nightly |

Locally the compose stack has no scheduler; the API's in-process sweep covers development.

### Where the data lives

- PostgreSQL: every tenant table has `tenant_id` and forced RLS (`migrations/0002_rls.sql` and
  every migration after it). Which module may write which table is declared in
  `config/ownership.yaml` and enforced by `tests/system/test_single_write_paths.py`.
- Object storage: per-tenant prefixes (`tenancy/storage.py`), uploads under `uploads/` and
  `quarantine/`, deliverables under the run's payload path.
- Secrets: Secret Manager in the cloud (containers created by Terraform, values added by the
  owner), `.env` locally. Nothing in an image or in Terraform state.

## 4. Where to change what

| I want to… | Go to |
| --- | --- |
| change a screen | `web/app/(client)/**` (client), `web/app/operator/**` (console), components in `web/components/**` |
| add or change an API endpoint | `src/account_intelligence/api/routes/*.py`, register the policy in `api/policy.py`, then `make contract` (regenerates `docs/api/openapi.json` and `web/lib/gen/`) |
| change how research works | `src/agents/` (graph, agents, prompts in each step's `prompt.md`), `config/agents.yaml`, the skill text in `config/skill/archive/` |
| change grading | `src/account_intelligence/grading/`, `config/grading.yaml`, `config/grading_policy.yaml` |
| change a deliverable file | `src/exports/` (word, excel, workspace), `config/export_rules.yaml`, `config/export_theme.yaml` |
| change the database | add `migrations/00NN_name.sql` (+ `.down.sql`) and the matching `migrations/versions/00NN_name.py`; declare the table owner in `config/ownership.yaml`; bump `EXPECTED_REVISION` in `api/routes/health.py` |
| change limits, prices, budgets | `config/*.yaml` (see `config/README.md`) |
| change the cloud | `infra/stack/main.tf` (sizes, queues, ticks), `infra/envs/staging/terraform.tfvars` (ignored; images and owner inputs), then plan and apply (`docs/deploy/RUNBOOK.md`) |
| add an alert or metric | `config/telemetry/*.yaml` → `scripts/gen_observability.py` → `infra/modules/observability/generated_*.tf.json` |
| see why a run stopped | `docs/runbooks/stuck-runs.md`; logs `jsonPayload.event="run.finished"` |

## 5. The gates (what must pass)

`make verify` runs all of them; CI runs the same set on every pull request and on `main`.

| Gate | Command | Enforces |
| --- | --- | --- |
| lint | `make lint` | ruff format + check, mypy strict over `src/` |
| static | `make static` | import layering, 100% docstrings, file headers, `GUARANTEE()` tags resolve, `LOOP()` annotations, bandit, vulture |
| contract | `make contract` | OpenAPI snapshot, generated web client, route catalog, wiring inventory |
| isolation | `make test-isolation` | cross-tenant probes against a real PostgreSQL (hard gate) |
| tests | `make test` | everything except evals, with coverage (94% floor) |
| obs | `make obs-verify` | telemetry contract, generated Terraform JSON, runbook lint |
| infra | `make infra-fmt infra-validate infra-test` | Terraform format, validate, offline plan |
| web | `npm --prefix web run typecheck / lint / test / build` | TypeScript, ESLint (no raw HTML), Vitest, production build |
| images | `make image && make smoke` | non-root images, `/readyz` 200, clean SIGTERM |

Repository-wide rules that tests enforce and that surprise newcomers: vendor names may appear
only in `config/providers.yaml` (client output says "6th agents"); guarded person names are
scanned by digest (`scripts/abm_name_scan.py`); every table has one writer
(`config/ownership.yaml`); `.env.example` must list exactly what the code reads.

## 6. Environments

| Environment | Where | How |
| --- | --- | --- |
| local | `make up` | compose.yml; hermetic by default (no provider or model call can happen) |
| local with real keys | `docker compose -f compose.yml -f compose.live.yml up -d` | reads `.env`; paid calls are possible |
| hermetic demo | `make replica-up` | compose.replica.yml, web :3010, api :8090, fake model |
| staging | Google Cloud project `abmgenerator`, us-central1 | Terraform root `infra/envs/staging`; services `ai-staging-api/worker/web`, job `ai-staging-migrate`; release steps in `docs/deploy/RUNBOOK.md` and `DEPLOYMENT.md` |
| production | `infra/envs/prod` | prepared, not applied; `deploy-prod.yml` needs the owner's approval |
