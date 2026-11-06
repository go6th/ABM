# 01 Architecture and planes

Verified against the code on 2026-11-03 (working tree of `feat/final-product`, uncommitted changes included).

See also: [index](README.md) · [02 request path and auth](02-request-path-and-auth.md) · [03 research run lifecycle](03-research-run-lifecycle.md) · [04 model routing](04-model-routing.md) · [05 spend and caps](05-spend-and-caps.md) · [06 data and isolation](06-data-and-isolation.md) · [07 documents and sandbox](07-documents-and-sandbox.md) · [08 exports and gate](08-exports-and-gate.md) · [09 ops](09-ops.md) · [10 known gaps](10-known-gaps.md)

## Three services, one Python application

| Service | What runs | Image / entry | Reached by |
|---|---|---|---|
| `web` | Next.js `16.4.1` (`web/package.json:18`), the browser's only origin and a BFF proxy for `/v1` | port 3000 (`infra/stack/main.tf:668-692`) | the load balancer: ingress `INTERNAL_LOAD_BALANCER` (`infra/stack/main.tf:672`) |
| `api` | FastAPI app `account_intelligence.api.main:app`, `SERVICE_ROLE=api` (`infra/stack/main.tf:597`) | `docker/api.Dockerfile:75` (uvicorn), Python 3.12 (`docker/api.Dockerfile:37`) | internal ingress only (`infra/stack/main.tf:595`); Cloud Run IAM invoker = the web service account (`infra/stack/main.tf:699-704`, ADR-0127) |
| `worker` | the SAME FastAPI app with `SERVICE_ROLE=worker` (`infra/stack/main.tf:630-636`) | worker image, uvicorn command overridden in Terraform (`infra/stack/main.tf:630`) | internal ingress (`infra/stack/main.tf:634`); invoker = the tasks service account that Cloud Tasks and Cloud Scheduler sign as (`infra/stack/main.tf:702`) |

The api and worker are one codebase: `create_app` registers routers by role (`src/account_intelligence/api/main.py:625-635`).
The worker image's own default is `SERVICE_ROLE=job` with the CLI entrypoint `python -m account_intelligence.worker` (`docker/worker.Dockerfile:78`, `docker/worker.Dockerfile:86`); only the Terraform `command` turns it into the HTTP worker.

## SERVICE_ROLE planes (ADR-0109)

`src/account_intelligence/api/planes.py` is the one table:

- `ROLE_PLANES`: `api` -> {api}, `worker` -> {worker}, `job` and `migrate` -> no plane, `all` -> both (`src/account_intelligence/api/planes.py:37-43`).
- A path's plane is its prefix: `/v1/` and `/operator/` are `api`, `/tasks/` is `worker`, `/healthz` and `/readyz` are served by every role (`src/account_intelligence/api/planes.py:45-50`, `:58-65`).
- `service_role()` defaults to `all`, refuses an unknown role, and refuses `all` when `APP_ENV` is `staging` or `production` (`src/account_intelligence/api/planes.py:73-84`). `create_app` calls it, so a misconfigured cloud process fails at import (`src/account_intelligence/api/main.py:625`).
- A route of another plane is never registered, so it answers 404 like an unknown path (`src/account_intelligence/api/planes.py:18-21`).
- `scripts/check_route_catalog.py` gives every route one plane and checks api + worker == the `all` app (`scripts/check_route_catalog.py:10-13`, `plane_problems` at `:94`); it runs in `make contract` (`Makefile:184`).
- Lifespan differences by role: only `api` in staging/production asserts signing keys (`src/account_intelligence/api/main.py:92-95`); only `worker` with `run_sweeper` on (`src/account_intelligence/config.py:337`) starts the background sweeper loop (`src/account_intelligence/api/main.py:141-145`).
- A cloud api with no task queue never runs a research job in-process: it logs `job_not_enqueued` and leaves the job for `runs.recovery` (`src/account_intelligence/replica/jobs.py:444-447`).

Terraform sets the role on every service and job: api (`infra/stack/main.tf:597`), worker (`:636`), `spend-invariants` and `reingest-tables` jobs as `job` (`:729`, `:770`), `migrate` as `migrate` (`:743`).

## Package layout under `src/`

All packages are listed in the wheel (`pyproject.toml:50-51`) and in import-linter's `root_packages` (`pyproject.toml:138`). One line each, from each package's own docstring:

**Core product (`account_intelligence`, "multi-tenant account research platform", `src/account_intelligence/__init__.py:1`):**
- `api/` FastAPI app, routes, catalogue, guards (`src/account_intelligence/api/__init__.py:1`); `api/routes/tasks.py` is the worker plane.
- `app/` the container, "the single place that chooses adapters" (`src/account_intelligence/app/__init__.py:1`).
- `workflow/` Workflow v1 spec, engine and the slice runner (`src/account_intelligence/workflow/__init__.py:1`; see page 03).
- `runs/` lifecycle, transitions, settlement, sweeper, recovery (`src/account_intelligence/runs/__init__.py:1`).
- `connectors/` broker layer: ledger, spend guard, research cache (`src/account_intelligence/connectors/__init__.py:1`).
- `grading/`, `tracker/`, `safety/` (`src/account_intelligence/grading/__init__.py:1`, `src/account_intelligence/tracker/__init__.py:1`, `src/account_intelligence/safety/__init__.py:1`).
- `platform/` ports with hermetic and GCP adapters, incl. the model port (`src/account_intelligence/platform/__init__.py:1`).
- `replica/` the product's client-facing read models, credit pool, jobs, schedules, workspace (`src/account_intelligence/replica/__init__.py:1`).
- `chat/` Ask/chat (`src/account_intelligence/chat/__init__.py:1`); `rag/` agentic retrieval (`src/account_intelligence/rag/__init__.py:1`); `db/` tenant transactions (`src/account_intelligence/db/__init__.py:1`); `worker/` one-off CLI commands (`src/account_intelligence/worker/__main__.py:31`).

**Research engine and outputs:**
- `agents/` the ABM agent layer: the skill's run as bounded agents (`src/agents/__init__.py:1`); `agents/production/wiring.py` plugs it into the runner (see `uses_abm`, page 03).
- `llm/` model routing table, user choice, the one price table (`src/llm/__init__.py:1`; page 04).
- `documents/` uploaded documents as a research input, input mode "file" (`src/documents/__init__.py:1`; page 07).
- `exports/` the standing file set from one snapshot (`src/exports/__init__.py:1`); `parity/` one AccountBundle, same numbers everywhere (`src/parity/__init__.py:1`) (page 08).
- `jobs/` job-posting pipeline (`src/jobs/__init__.py:1`); `people/` people pipeline (`src/people/__init__.py:1`); `verify/` downgrade-only verifier agents (`src/verify/__init__.py:1`); `parallel/` dependency-aware unit scheduler (`src/parallel/__init__.py:1`); `research_files/` (`src/research_files/__init__.py:1`).

**Money, tenancy, identity, operations:**
- `spend/` credit cap v1, one cap per run (`src/spend/__init__.py:1`); `metering/` insert-only usage events and plan limits (`src/metering/__init__.py:1`) (page 05).
- `tenancy/` settings, storage paths, lifecycle (`src/tenancy/__init__.py:1`); `identity/` OIDC/SAML broker, sessions, CSRF, roles (`src/identity/__init__.py:1`); `admin/` the operator plane incl. feature flags (`src/admin/__init__.py:1`); `audit/` hash-chained audit (`src/audit/__init__.py:1`); `views/` client tables (`src/views/__init__.py:1`).
- `obs/` telemetry contract, traces, SLOs (`src/obs/__init__.py:1`); `evalgate/` the eval harness (`src/evalgate/__init__.py:1`); `crosscut/` requirements as data checked against the repo (`src/crosscut/__init__.py:1`; its spec is `config/section11_spec.yaml`).
- Smaller: `connectors_client/`, `crm_writeback/` (routes behind the `crm` flag, page 02), `opendoor/`, `pilot/`, `datamodel/`, `devstore/` (dev/test only, `src/devstore/__init__.py:1`).

**Built but not wired (do not treat as live):**
- Monitoring product: `src/refresh`, `src/signals`, `src/alerts`, and `governance/` (monitoring cost governance, `src/governance/__init__.py:1`). The only live importer is the flag-gated route module `src/account_intelligence/api/routes/signals.py` (every route behind `monitoring`, `src/account_intelligence/api/routes/signals.py:47-48`). ADR-0134 records this.
- `src/privacy`, `src/quality`, `src/contracts` were parked to `unwanted-things/dead-code-2026-11/` and left every `pyproject.toml` package list (`docs/decisions/ADR-0136-dead-code-and-config-registers-parked.md`).

## Import-linter layering

Run by `make static` (`Makefile:104-105`; CI `.github/workflows/ci.yml:93`). Contracts in `pyproject.toml`, all `forbidden` type:

- `runs`, `identity`, `metering`, `app` never import `api`, with a 5-entry ratchet of existing violations (`pyproject.toml:141-155`).
- Nothing below the ABM agent layer imports `agents` (platform, connectors, grading, tracker, workflow, runs, jobs, people, spend, exports, llm, verify, connectors_client) (`pyproject.toml:158-166`).
- `platform` depends on nothing above it (`pyproject.toml:169-176`); `connectors` never import tracker/grading/workflow/runs/api/app/jobs (`:179-185`); `grading` (`:188-194`), `tracker` (`:197-203`), `jobs` (`:206-212`); `workflow` and `runs` never import api/app (`:215-218`).
- `spend` never imports api/app/runs/workflow except `spend.plan -> workflow.spec` (`pyproject.toml:221-228`); `exports` never imports api/app/runs (`:231-236`) and nothing below the builders imports `exports` (`:239-245`).
- Only an allow-list of 17 modules may import `account_intelligence.db.system_scope` (RLS bypass, ADR-0123) (`pyproject.toml:252-275`).
- `replica` and `documents` never import api/app, one ratchet entry (`pyproject.toml:278-286`); `runs` never imports `workflow` (`:308-311`).
- `rag` (`:314-322`), `parallel` (`:325-332`), `llm` (`:335-342`), `verify` (`:345-351`), `evalgate` (`:354-360`) are lower layers.
- Pure/domain packages never import `obs` (`pyproject.toml:363-369`); `obs` never imports api/app/workflow (`:372-375`). This is why `dev_limits` is pure and the caps-off warning lives in `app.container` (page 05).

## Cloud Run, Cloud Tasks, Cloud SQL shape

- **Sizes** (`infra/stack/main.tf:226-243`): api 1 vCPU / 2Gi / concurrency 40; web 1 vCPU / 512Mi / 40; worker 4 vCPU / 12Gi / **concurrency 1** (ADR-0130, was 4), all with a 3600 s request timeout. The worker keeps CPU between requests (`cpu_idle = false`, `infra/stack/main.tf:652`) for the background sweeper, and carries a `clamd` sidecar (`infra/stack/main.tf:659-666`).
- **Cloud Tasks** (`infra/stack/main.tf:249-286`): three queues sized from `worker_slots = scaling.worker.max x concurrency` (`:245`): `runs` (half, 8 attempts, 10-300 s backoff, `:254-267`), `documents` (a quarter, 20 attempts, `:268-277`), `jobs` (a quarter, 5 attempts, `:278-285`). The adapter is `CloudTasksScheduler` with deterministic task names `run-{id}-slice-{n}` and `job-{id}` (`src/account_intelligence/platform/gcp/adapters.py:87-100`, `:162-172`, `:191-197`). Every task is OIDC-signed for the worker audience and verified in `_authorise` (`src/account_intelligence/api/routes/tasks.py:83-110`).
- **Cloud Scheduler** (`infra/stack/main.tf:788-816`): one Cloud Run Job tick (`spend-invariants`, `:799`) and eight HTTP ticks to `/tasks/*`: schedules-tick (15 min), isolation-check, access-expiry, maildrive-sync, sweep (5 min), audit-verify, notify-relay, metrics (`:805-814`). No retention or alert-digest tick (ADR-0132).
- **Cloud Run Jobs** (`infra/stack/main.tf:715-786`): `spend-invariants`, `migrate` (`alembic upgrade head`, `:739`), `reingest-tables`.
- **Cloud SQL**: PostgreSQL 16, Enterprise edition, private IP only (`infra/modules/cloud_sql/main.tf:84`, `:89`, `:97-98`); IAM database users for api, worker, job (`infra/stack/main.tf:462`); backups 7 days staging / 30 prod (`:463`). Staging tier is a variable whose current value `db-custom-1-3840` is named only in a comment (`infra/envs/staging/main.tf:32-39`). LangGraph checkpoints live in Postgres (runner docstring, `src/account_intelligence/workflow/runner.py`).
- **Memorystore Redis** for rate limits (`infra/stack/main.tf:481`, `infra/modules/cache/main.tf:41`).

## Code vs ADR

- ADR-0130 and ADR-0131 say the Terraform changes (worker concurrency 1, alerts) and ADR-0127 (api invoker = web only) are "planned, NOT applied". The `.tf` files on disk already carry them (`infra/stack/main.tf:242`, `:699-704`); whether staging matches cannot be read from the repo.
- `infra/stack/main.tf:224` still says run slices are "240 s each"; the configured slice is 1650 s (`config/workflow_v1.yaml:13`).
- `src/account_intelligence/worker/__init__.py:1` says "one container per account run (Cloud Run Jobs)"; runs execute as `/tasks/run-slice` requests on the worker service (`src/account_intelligence/api/routes/tasks.py:113-149`), and the CLI only has four maintenance commands (`src/account_intelligence/worker/__main__.py:31`).
