# 09 Operations: release, infrastructure, scheduler, alerts, local dev, tests

Verified against the code on 2026-11-03.

See also: [README](README.md) · [01 Architecture and planes](01-architecture-and-planes.md) · [02 Request path and auth](02-request-path-and-auth.md) · [03 Research run lifecycle](03-research-run-lifecycle.md) · [04 Model routing](04-model-routing.md) · [05 Spend and caps](05-spend-and-caps.md) · [06 Data and isolation](06-data-and-isolation.md) · [07 Documents and sandbox](07-documents-and-sandbox.md) · [08 Exports and gate](08-exports-and-gate.md) · [10 Known gaps](10-known-gaps.md)

## 1. Releases

The repository has **two** paths, and the branch in use decides which one applies.

**A. Automated workflow (designed path, main only).**
- `ci.yml` runs on pull requests and on pushes to `main` and `feat/**` (`.github/workflows/ci.yml:11-14`).
- `deploy-staging.yml` runs when `ci` completes successfully for a **push to `main`**, or by hand with a commit SHA that has a green CI run on main (`.github/workflows/deploy-staging.yml:6-16`, `.github/workflows/deploy-staging.yml:29-32`).
- It calls `_deploy.yml`, which does the following (`.github/workflows/_deploy.yml:1-12`, `.github/workflows/_deploy.yml:131-164`):
  1. authenticates with Workload Identity Federation;
  2. pushes the images CI built;
  3. runs the migrate job;
  4. deploys worker, api and web by digest;
  5. runs `docker/ci/smoke_deployed.sh`;
  6. on a failed smoke test, routes traffic back to the previous revisions.
- Production is `deploy-prod.yml`, `workflow_dispatch` only with a SHA (`.github/workflows/deploy-prod.yml:7-13`).
- The project, region, service names and WIF provider are GitHub environment variables (`.github/workflows/_deploy.yml:47-55`, `.github/workflows/_deploy.yml:83-85`). Whether they are set cannot be seen from the repo.

**B. Manual Cloud Build + gcloud (how this branch is actually released).** Work happens on `feat/final-product`, and `deploy-staging.yml` only fires for `main`, so releases from this branch are done by hand.
- The recipe is section 2.2 of `DEPLOYMENT.md`:
  1. one `gcloud builds submit` per image (api and worker from a generated cloudbuild config, web with `--tag`);
  2. `make release-check` on the pushed digests (image checks plus Trivy);
  3. `gcloud run jobs update/execute ai-staging-migrate`;
  4. `gcloud run services update` for worker, api and web;
  5. `make release-check` again with `APP_URL` set, which runs the deployed smoke test (`DEPLOYMENT.md`).
- `DEPLOYMENT.md` section 2.2 is titled as the path in use until the branch reaches `main`, and section 2.1 says the workflow has not released this branch (`DEPLOYMENT.md`).
- There is no `cloudbuild*.yaml` in the repo; the build configs are written to `/tmp` by that recipe (`DEPLOYMENT.md`).
- `make release-check` runs `docker/ci/image_checks.sh` for each image, Trivy (fixable HIGH and CRITICAL), and the smoke test when `APP_URL` is set (`Makefile:157-165`).
- After any release, write the digests into `infra/envs/staging/terraform.tfvars`. The Cloud Run module ignores image changes, so Terraform never rolls a service back (`DEPLOYMENT.md`).
- Rollback is in `docs/deploy/RUNBOOK.md` and `RUNBOOK.md`.
- `make deploy-staging` only prints commands (`Makefile:144-145`): `scripts/deploy_staging.sh` prints the bash block of `DEPLOYMENT.md` section 2.2, so the printed recipe is the document's (`scripts/deploy_staging.sh`).

## 2. Google Cloud layout

- Region: staging uses `us-central1`, project `abmgenerator` (`infra/envs/staging/terraform.tfvars:5-6`). That file is git-ignored (`infra/README.md:9-10`); the committed example leaves the region as a placeholder (`infra/envs/staging/terraform.tfvars.example:5`).
- Images: three app images, `api`, `worker` and `web` (`infra/envs/staging/main.tf:26-28`), plus a pinned ClamAV image for the worker's clamd sidecar (`infra/envs/staging/main.tf:29-31`, `infra/stack/main.tf:660-665`). All four are in `us-central1-docker.pkg.dev/abmgenerator/ai-staging-images` (`infra/envs/staging/terraform.tfvars:10-15`).
- Names are `ai-<environment>-...` (`infra/stack/main.tf:212`).
- Cloud Run services:
  - api: internal ingress (`infra/stack/main.tf:595`);
  - worker: internal, the same app with `SERVICE_ROLE=worker`, CPU always on for the in-process sweeper (`infra/stack/main.tf:626-667`);
  - web: internal and load balancer (`infra/stack/main.tf:672`).
- Cloud Run jobs: `spend-invariants`, `migrate` (`alembic upgrade head`) and `reingest-tables`, the last run by hand (`infra/stack/main.tf:723-777`).
- Cloud Tasks queues: `runs` (8 attempts), `documents` (20 attempts), `jobs` (5 attempts) (`infra/stack/main.tf:253-285`).
- Terraform envs:
  - `infra/envs/staging` is applied: it has a `terraform.tfvars` and a saved `staging.plan`;
  - `infra/envs/prod` is prepared, not applied: there is only `main.tf` and the example (`infra/README.md:11`).
  - Both call `infra/stack/main.tf`, which composes the modules in `infra/modules/*` (`infra/README.md:1-11`).
  - State is in a GCS backend given at `init` (`infra/envs/staging/main.tf:1-9`).
  - Changes go through `terraform plan -out` and then an apply of that plan file (`DEPLOYMENT.md`).
  - Offline checks: `make infra-fmt`, `make infra-validate`, `make infra-test` (`Makefile:114-134`).
- Warning: staging `archive_lock` defaults to `true` (`infra/envs/staging/main.tf:102-105`), and `terraform.tfvars` does not override it. The next apply would **irreversibly** lock the audit archive's 7-year retention policy (`docs/decisions/ADR-0125-secrets-iam-checkpointer-redis-tls.md:56-65`). See [10](10-known-gaps.md).

## 3. Cloud Scheduler entries

All entries run in `Etc/UTC` (`infra/stack/main.tf:794`) with one retry (`infra/modules/scheduler/main.tf:59-61`, `infra/modules/scheduler/main.tf:79-82`). HTTP ticks POST `{}` to the worker URL with an OIDC token (`infra/modules/scheduler/main.tf:83-93`). The job entry calls the Cloud Run Jobs API with OAuth (`infra/modules/scheduler/main.tf:62-69`).

| Entry | Schedule (UTC) | Target | Deadline | Source |
|---|---|---|---|---|
| spend-invariants | `17 3 * * *` | Cloud Run job `spend-invariants` | 320 s | `infra/stack/main.tf:799` |
| schedules-tick | `*/15 * * * *` | `POST /tasks/schedules-tick` | 300 s | `infra/stack/main.tf:806` |
| isolation-check | `0 2 * * *` | `POST /tasks/isolation-check` | 1800 s | `infra/stack/main.tf:807` |
| access-expiry | `30 3 * * *` | `POST /tasks/access-expiry` | 900 s | `infra/stack/main.tf:808` |
| maildrive-sync | `5 * * * *` | `POST /tasks/maildrive-sync` | 900 s | `infra/stack/main.tf:809` |
| sweep | `*/5 * * * *` | `POST /tasks/sweep` | 300 s | `infra/stack/main.tf:810` |
| audit-verify | `15 4 * * *` | `POST /tasks/audit-verify` | 1800 s | `infra/stack/main.tf:811` |
| notify-relay | `*/5 * * * *` | `POST /tasks/notify-relay` | 300 s | `infra/stack/main.tf:812` |
| metrics | `*/2 * * * *` | `POST /tasks/metrics` | 120 s | `infra/stack/main.tf:813` |

- None of the entries is paused (`infra/stack/main.tf:805-814`).
- The `retention` and `alert-digest` entries were removed: their routes were never built (`infra/stack/main.tf:801-804`).
- Every target route exists in `src/account_intelligence/api/routes/tasks.py` (`src/account_intelligence/api/routes/tasks.py`, `run_slice` … `isolation_check`).
- The worker also runs its own sweeper loop in-process (`src/account_intelligence/runs/sweeper.py:20-30`).

## 4. Alerts

- **Hand-written policies**, which work from the first deploy (`infra/modules/observability/platform.tf`):
  - uptime for web `/healthz`, and for api through web `/readyz` (`infra/modules/observability/platform.tf:55-58`, `infra/modules/observability/platform.tf:90`);
  - 5xx errors (`infra/modules/observability/platform.tf:121`) and p95 latency (`infra/modules/observability/platform.tf:148`);
  - task backlog (`infra/modules/observability/platform.tf:178`);
  - Cloud SQL CPU, saturation, disk, memory and connections (`infra/modules/observability/platform.tf:208-234`);
  - migration failed (`infra/modules/observability/platform.tf:261`);
  - Cloud Scheduler failures (`infra/modules/observability/platform.tf:354-359`);
  - log-event alerts on `isolation_check_failed`, `runs.stalled`, `runs.swept`, `runs.sweep_failed` and `rate_limit_degraded` (`infra/modules/observability/platform.tf:47-54`, `infra/modules/observability/platform.tf:321`).
- **Generated policies.** `scripts/gen_observability.py` turns `config/telemetry/alerts.yaml` into `generated_alerts.tf.json`, created only when `workload_alerts_enabled = true` (`infra/modules/observability/main.tf:7-11`, `infra/modules/observability/generated_alerts.tf.json:16`). Staging sets it to true (`infra/envs/staging/terraform.tfvars:50`).
  - The catalog is marked `status: proposal`, and most thresholds say "proposal" (`config/telemetry/alerts.yaml:5`).
  - `refresh_scheduler_stalled` is paused because the refresh scheduler is not deployed (`config/telemetry/alerts.yaml:26`).
- **Nobody is notified.** Staging has `alert_channels = []` (`infra/envs/staging/terraform.tfvars:32`), and that list feeds both page and ticket channels (`infra/stack/main.tf:833-834`).
- Check the generated Terraform is current with `make obs-verify` (`Makefile:122`).

## 5. Local development

- `make up` builds and starts `compose.yml`: db (pgvector pg16), gcs and tasks emulators, redis, migrate, worker, fake-idp, api and web (`Makefile:29-30`, `compose.yml:33-150`). All ports are bound to `127.0.0.1` (`compose.yml:36`, `compose.yml:139`, `compose.yml:150`).
- Ports: web :3000, api :8080, worker :8081, Postgres :5432, fake IdP :9090 (`compose.yml:36-150`).
- The stack is hermetic by default; `compose.live.yml` adds real keys (`DEPLOYMENT.md`).
- Other targets (`Makefile:32-67`):
  - `make up-deps` starts only db, emulators and Redis, for backend tests;
  - `make down`, `make reset` (deletes volumes), `make logs`, `make db-shell`;
  - `make migrate`;
  - `make run` (uvicorn with reload on :8080, reads `.env`).
- Replica stack: `make replica-up` (web :3010, api :8090, `compose.replica.yml`) (`Makefile:38-39`).
- Diagnose the toolchain with `make doctor` (`Makefile:224`).

## 6. Tests

- Always pass `-m "not eval and not live"`. `pyproject.toml` sets it in `addopts` with `--strict-markers` (`pyproject.toml`), but any explicit `-m` on the command line replaces it. `live` tests call the real paid model.
- `make test` runs the isolation suite first (`pytest tests/isolation -x`), then `pytest -m "not eval and not live" --cov` (`Makefile:83-88`). `TEST_ENV` points at a scratch database, `TEST_DB` (default `ai_test`) (`Makefile:7`, `Makefile:11`).
- One pytest session per test database: the fixture drops and recreates it (`tests/conftest.py:50-55`).
- Pre-PR gate: `make check` (`Makefile:97-100`). Broader local gate: `make verify` (`Makefile:196-198`).
- CI runs the same order (`.github/workflows/ci.yml:127-128`).
- `eval.yml`: replay evals on every pull request. The **live** tabular eval runs nightly at 02:17 UTC, on demand, and on model or routing changes (`.github/workflows/eval.yml:1-15`, `.github/workflows/eval.yml:86`).

## Code vs docs

- A push-to-main release does exist: `deploy-staging.yml` deploys after a green CI on a push to `main` (`.github/workflows/deploy-staging.yml:6-10`). It just never fires for `feat/final-product`.
- `docs/open-items.md` V-REGION-1 still asks about asia-south1 (`docs/open-items.md:9`).
