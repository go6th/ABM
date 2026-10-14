# Agent 3 "gcp-infra": report

Branch `agent/gcp-infra` (from `feat/replica` at b132645), worktree
`/home/sakib/abm-agent-gcp-infra`. Nothing was applied, deployed, imported or destroyed; no
gcloud command was run; no real credential was mounted or used; no paid model or provider call.

## 1. What I did

Read: docs/deploy/PLAN.md, FIX_PROMPT (read only), every file under `infra/`,
`platform/gcp/adapters.py`, `config.py`, `docs/ENVIRONMENT.md`, `api/routes/tasks.py`,
`app/container.py`, `api/main.py`, `research_files/blobstore.py`, `documents/service.py` and
`scan.py`, `config/research_input_modes.yaml`, `config/workflow_v1.yaml`, `worker/__main__.py`,
the Dockerfiles, the deploy workflow and script, docs/DEPLOY.md and docs/runbooks/*, ADR-0018 and
ADR-0020, and the security audit items M3, M6, L16 (plus M1 and M5 for coordination).

Where the existing Terraform had chosen something I kept it (ARCHITECTURE.md section 2 lists
what was kept and what changed and why).

| Area | Change |
| --- | --- |
| Cloud Run | New private **worker** service (worker image run as `uvicorn ... api.main:app`, `SERVICE_ROLE=worker`): ingress internal, run.invoker for the tasks identity only, fixed OIDC audience `ai-ENV-worker-tasks`, gen2, CPU always allocated (background sweeper), 4 vCPU / 8 GiB / concurrency 4 / 3600 s, clamd sidecar (1 vCPU / 4 GiB; `DOCUMENT_SCANNER=clamd`), data bucket mounted read only at `/mnt/blobs`. api and web: per-env min/max, startup probe `/readyz` (api) or `/healthz` (web), liveness `/healthz`, web timeout 300 s (Ask streams). Terraform ignores image and traffic (rollbacks survive an apply). `WORKER_URL` now points at the worker. |
| api reachability | Internal ingress from web (not the load balancer), reasons in ARCHITECTURE.md 2. |
| Jobs | `migrate` and `spend-invariants` only. The `run` job is removed: nothing reads `RUN_JOB`, runs are worker requests (ADR-0018). |
| Cloud SQL | `edition = ENTERPRISE` (PG16 defaults to Enterprise Plus, which refuses db-custom tiers: apply would have failed), `max_connections` variable, logging and pgaudit flags (static blocks), maintenance window, SSD with autoresize; private IP, IAM auth, PITR, backups, deletion protection kept; HA (`db_availability`) is an env variable (prod default REGIONAL); IAM users for api, worker and the nightly job. |
| Memorystore | TLS port output (6378: the old `REDIS_URL` used 6379, which a TLS instance does not serve), server CA mounted as a file (`redis-server-ca`), tier variable (prod STANDARD_HA), Redis 7.2, maintenance window. |
| Cloud Storage | CORS for the web origin only (PUT/POST/GET/HEAD; Content-Type, Content-Range, Range), lifecycle for `quarantine/` (7 d), `uploads/` (3 d), aborted multipart (2 d), raw files and noncurrent versions; 7-year **audit archive** bucket (ARCHIVE class, CMEK, retention policy, unlocked unless `archive_lock`, worker may only create objects). |
| Cloud Tasks | Three queues `runs`, `documents`, `jobs` with rates, concurrency tied to the worker maximum, retries, full dispatch logging. |
| Scheduler | OIDC ticks to the worker as the tasks identity: schedules-tick (15 min), isolation-check (02:00), access-expiry (03:30), maildrive-sync (hourly); created **paused** for Phase 5: sweep, retention, alert-digest, audit-verify. The spend-invariants job trigger kept. |
| Secrets | Reader lists tightened: provider keys worker only; anthropic and moonshot api+worker (read through the API); checkpoint URL api, worker, job; new `csrf-signing-key` (api only, audit M6); `moonshot-api-key`; mail and drive client secrets created and wired only when their client id is set; `MOONSHOT_SECRET` set only with `moonshot_enabled`. |
| IAM | New `worker` account; one account per workload (api, worker, job, web, migrate, tasks, scheduler, deployer); Vertex role only for api and worker; api and worker may act as the tasks identity; default compute service account disabled; WIF condition also pins `sub`. |
| Edge | OWASP CRS 3.3 sets at sensitivity 1 (sqli, xss, lfi, rfi, rce, protocol attack, session fixation, scanner detection, method enforcement), log4j rule, JSON parsing, Adaptive Protection, TLS 1.2+ MODERN SSL policy; **audit M3**: the sign-in rule now also covers password reset and reset request, invite accept, SSO discover and start, plus a 60/min rule for `/api/operator/(login|callback|session)`; `/_next/static/` exempt from the per-client limit; that limit is a variable (default 1200/min). |
| Network | Explicit deny-all ingress rule, subnet flow logs at 10 percent, NAT with dynamic ports (64 to 4096). |
| Registry | Cleanup policies (keep 30 most recent, delete older than 90 d, untagged older than 14 d). |
| Monitoring | Observability module wired into the stack (it was not before). New `platform.tf`: uptime checks (web `/healthz`, api through web `/readyz`), alerts on 5xx, p95 latency, task backlog, database CPU/memory/connections/disk, failed migrations, isolation check failed, any failed Scheduler tick, runs stalled, runs worker_lost, sweeper failures; a platform dashboard. |
| Generator | `scripts/gen_observability.py` (and only `infra/modules/observability/generated_alerts.tf.json`): every generated alert gets `count = var.workload_alerts_enabled ? 1 : 0`, because they watch `workload.googleapis.com` metrics that do not exist until the OTLP exporter ships (V-OBS-1) and Monitoring refuses alert policies on unknown metrics. Dashboards unchanged. |
| Budget | Alerts at 50, 90 and 100 percent of actual spend plus 100 percent forecast; amount must be positive. |
| Variables | Per env: `db_availability`, `db_max_connections`, `redis_tier`, `scaling`, `clamav_image` (digest pinned), `moonshot_enabled`, `maildrive`, `archive_lock`, `workload_alerts_enabled`; tfvars examples rewritten (placeholders only). |
| Web | `UPLOAD_ORIGIN=https://storage.googleapis.com` (audit note 4). |
| Audit L16 | Runtime IAM users get `app_rw` only; documented (ARCHITECTURE.md 2) and a post-deploy query (RUNBOOK.md 6.4); the migrate job gets `APP_RW_IAM_USERS`. |
| Tests | `stack/tests/plan.tftest.hcl` extended (8 runs); new module tests for cloud_run, gcs, cloud_sql, scheduler, tasks (iam test updated for the `sub` pin). |
| Tooling | `infra/.tflint.hcl` (terraform recommended preset + google ruleset 0.30.0); `versions.tf` with provider constraints in each module. |
| Docs | `docs/deploy/ARCHITECTURE.md` (design, sizing arithmetic, connection arithmetic, security, monitoring, cost estimates), `docs/deploy/RUNBOOK.md` (one-time setup, secrets, OAuth apps, deploy, rollback to revision and to a restore point, rotation, restore, post-deploy checks incl. V-FILES-1, V-SQL-1, V-TASKS-1, V-REDIS-1, L16). |

## 2. Checks run (all in throwaway containers, worktree mounted, empty `CLOUDSDK_CONFIG`, no `GOOGLE_APPLICATION_CREDENTIALS`)

Wrapper used for terraform (scratchpad `tf.sh`):
`docker run --rm --user UID:GID -v /home/sakib/abm-agent-gcp-infra/infra:/infra -v SCRATCH/tfcache:/tfcache -e HOME=/tmp -e CLOUDSDK_CONFIG=/tmp/empty -e TF_PLUGIN_CACHE_DIR=/tfcache -e GOOGLE_APPLICATION_CREDENTIALS= -w /infra/DIR hashicorp/terraform:1.9.8 ...`

| Check | Command | Result |
| --- | --- | --- |
| fmt | `terraform fmt -check -recursive` (in `infra/`) | pass (exit 0) |
| init | `terraform init -backend=false -input=false` per root and per tested module | ok (google and google-beta 6.50.0, as locked) |
| validate | `terraform validate` in `stack`, `envs/staging`, `envs/prod` | "Success! The configuration is valid." for all three |
| stack test (mock providers) | `terraform test` in `infra/stack` | 8 passed, 0 failed (staging plan, secret readers, opt-in mail/moonshot, clamav digest, image digest, unknown env, unknown model access, prod plan) |
| module tests | `terraform test` in `modules/{cloud_run,gcs,cloud_sql,scheduler,tasks,iam,secret_manager}` | 2, 2, 2, 1, 2, 1, 1 passed; 0 failed |
| plan, staging and prod | see section 3 | **195 to add, 0 to change, 0 to destroy** each |
| tflint 0.64.0 | `docker run --rm -e TFLINT_PLUGIN_DIR=/plugins -v .../infra:/data:ro -w /data ghcr.io/terraform-linters/tflint:latest --init`, then `--recursive --format compact` | first run 32 warnings (missing `required_version` and provider version constraints in 15 modules): fixed with `versions.tf`; final run: 0 issues |
| checkov 3.3.29 | `docker run --rm -v .../infra:/infra:ro bridgecrew/checkov:latest -d /infra --quiet --compact --framework terraform` | first run 19 failed; after fixes **60 passed, 0 failed, 18 skipped** (9 explained skips, counted once per env root) |
| generator | `python -B scripts/gen_observability.py` then `--check` (replica venv's interpreter) | regenerated; check exit 0 |
| generator tests | `pytest -q -p no:cacheprovider tests/obs/test_contract_and_pure.py` | 26 passed |
| lint | `ruff check` and `ruff format --check` on `scripts/gen_observability.py` | clean |

### checkov findings: fixed

| Check | Fix |
| --- | --- |
| CKV_GCP_51/52/53/54/108/109/110/111 (PostgreSQL logging flags, pgaudit) | flags written as static blocks (checkov cannot read the earlier `dynamic` map) and `log_hostname`, `log_statement=ddl`, `log_min_messages=error` added |
| CKV_GCP_73 (log4j) | Cloud Armor rule `evaluatePreconfiguredExpr('cve-canary')` |
| CKV2_GCP_18 (VPC without a firewall) | explicit deny-all ingress rule |
| CKV_GCP_125 (GitHub OIDC trust) | `assertion.sub == "repo:OWNER/REPO:environment:ENV"` added to the WIF condition (still reported because checkov cannot resolve the variable: skip below) |

### checkov findings: explained (inline `checkov:skip` with the reason)

| Check | Resource | Why it stays |
| --- | --- | --- |
| CKV_GCP_6 | Cloud SQL | `ssl_mode = ENCRYPTED_ONLY` refuses every unencrypted connection; checkov only accepts client-certificate mode, which the connector (own mTLS) and the checkpointer (`sslmode=require`) do not use |
| CKV_GCP_79 | Cloud SQL | PostgreSQL 16 is what dev, test and the replica run; upgrade all together |
| CKV2_GCP_13 | Cloud SQL | `log_duration` logs every statement; Query Insights records latency |
| CKV_GCP_62 (x2) | data and archive buckets | access is in Cloud Audit Logs; usage-log buckets add cost and another copy of key names |
| CKV_GCP_78 | archive bucket | retention policy and versioning are mutually exclusive; the retention lock is the protection |
| CKV_GCP_84 | Artifact Registry | the service offers CMEK, not CSEK; images hold no tenant data and are signed and scanned |
| CKV_GCP_95 | Memorystore | AUTH would put its string in Terraform state; private VPC only |
| CKV_GCP_125 | WIF provider | `sub`, repository, ref and environment are pinned (module test); checkov cannot resolve `var.github_repository` |

## 3. The plan, exactly what worked

- `terraform test` with mock providers: works fully offline (section 2).
- A real `terraform plan` of `infra/envs/staging` in the worktree is refused without a backend
  init ("Backend initialization required"), and a gcs backend init was not allowed. So I copied
  `infra/` (without `.terraform`) to the scratchpad, added a `backend_override.tf` with a local
  backend there, and ran `terraform init` then
  `terraform plan -refresh=false -lock=false -var-file=/tfvars/ENV.tfvars` with
  `GOOGLE_OAUTH_ACCESS_TOKEN=placeholder` and an empty `CLOUDSDK_CONFIG`, placeholder tfvars
  outside the repository (project `placeholder-staging` / `placeholder-prod`).
- That plan failed only on the two data sources, which call Google APIs even with
  `-refresh=false`: `data.google_project.this` and
  `data.google_storage_project_service_account.gcs` (401 "Invalid Credentials", as intended:
  nothing real can be reached). In the scratch copy only, I replaced them with literal values
  (project number `123456789012`, the GCS service agent email) and planned again:
  **staging: 195 to add, 0 to change, 0 to destroy; prod: 195 to add, 0 to change, 0 to
  destroy** (no state exists, so everything is an add). By type: 23 project IAM members,
  21 enabled services, 19 secret accessor bindings, 15 alert policies (the 27 generated ones are
  at count 0), 14 secrets, 9 Scheduler jobs, 8 service accounts, 8 account IAM members,
  8 dashboards, 6 log metrics, 5 KMS key bindings, 3 Cloud Run services, 3 queues, 3 SQL users,
  3 bucket bindings, 3 service invoker bindings, 2 jobs, 2 buckets, 2 uptime checks, one each of
  SQL instance, database, Redis, network, subnet, firewall, router, NAT, NAT address, PSA
  range and connection, load balancer pieces, SSL policy, Armor policy, registry, budget, WIF
  pool and provider, Identity Platform, key ring and keys, the Redis CA secret version.
- No `.terraform/`, plan file or tfvars is committed (`.gitignore` already covers them). The new
  module lock files (`modules/{cloud_run,cloud_sql,gcs,scheduler,tasks}/.terraform.lock.hcl`) are
  committed like the existing iam and secret_manager ones.

## 4. What I could not do

- No apply, so none of the VERIFY items is resolved: V-FILES-1, V-SQL-1, V-TASKS-1, V-REDIS-1 have
  staging steps in RUNBOOK.md section 6; V-REGION-1 in 1.1; V-OBS-1 gates the workload alerts.
- Real API validation of values the mock providers accept blindly (for example Cloud Armor
  expression syntax, Monitoring filter syntax, Cloud Run sidecar limits, custom audience strings)
  happens only at apply on staging. The first staging apply is the real test of those.
- Prices in ARCHITECTURE.md section 6 are estimates from list prices I know, not a calculator run.
- The four Phase 5 Scheduler paths (`/tasks/sweep`, `/tasks/retention`, `/tasks/alert-digest`,
  `/tasks/audit-verify`) are my proposals; the ticks are paused until those routes exist.

## 5. Owner inputs (names only)

Per environment: project id, region, billing account id, domain (and its DNS A record), GitHub
repository, the Terraform state bucket, monthly budget amount, notification channel ids,
`raw_retention_days`, database tier, `llm_access`, staff Workspace domain and allow-lists, staff
OAuth client id, the mirrored ClamAV image digest, optionally mail and drive client ids,
`moonshot_enabled`, `archive_lock`. Secret values (RUNBOOK.md 1.4): `checkpoint-database-url`,
`migrator-database-url`, `app-rw-password`, `csrf-signing-key`, `operator-google-client-secret`,
`anthropic-api-key` (with anthropic_api), the six `p-*` provider secrets, optionally
`moonshot-api-key`, `maildrive-google-client-secret`, `maildrive-microsoft-client-secret`. The
`postgres` and `migrator` database passwords (RUNBOOK.md 1.6). OAuth apps: staff console, mail
and drive (Google, Microsoft). GitHub environment variables (RUNBOOK.md 1.3).

## 6. Handoffs

### To Agent 4 (backend-hardening)

1. **CSRF key (M5/M6)**: `src/account_intelligence/api/tenant_plane.py:31`: the Secret Manager id
   is **`csrf-signing-key`**; the api service has env **`CSRF_KEY_SECRET=csrf-signing-key`**. Read
   the id from `CSRF_KEY_SECRET` (register it in `config.py`, default `csrf-signing-key`) or
   hard-code `csrf-signing-key`; either matches. Line 32 (`tenant-plane/download-key`) is also not
   a valid id; it is unused, so no secret was created for it.
2. **Pool size**: `src/account_intelligence/platform/gcp/adapters.py:218`: pass
   `pool_size=DB_POOL_SIZE` and `max_overflow=DB_MAX_OVERFLOW` to `create_async_engine`; the stack
   sets 5 and 5 (ARCHITECTURE.md 3 assumes them). Today the cloud engine uses 5 + 10.
3. **Queues**: register `TASKS_QUEUE_DOCUMENTS` and `TASKS_QUEUE_JOBS` (set on api, worker and the
   nightly job) and use them in `CloudTasksScheduler` (`adapters.py:129` `enqueue_file`, `:138`
   `enqueue_document` -> documents; `:149` `enqueue_research_job`, `:154` `enqueue_download` ->
   jobs; slices and `:161` approval timeout stay on `TASKS_QUEUE`).
4. **Dispatch deadline (V-TASKS-1)**: set `Task.dispatch_deadline` explicitly in `_task`
   (`adapters.py:88`): 600 s for slices and jobs, 1800 s (the maximum) for documents.
5. **One part per request**: `src/documents/service.py:468` `read_parts` loops over every part in
   one request, which can exceed the 30-minute Cloud Tasks maximum; read one part, then enqueue
   the next (as run slices do), or cap `sandbox.timeout_s` below about 1500 s.
6. **No whole-file temp copy**: `src/documents/service.py:638` `_local` copies the object to a
   temp file, which is RAM on Cloud Run. The worker mounts the bucket read only at
   `BLOB_MOUNT_DIR=/mnt/blobs` (env set): register it and give `GcsBlobStore`
   (`src/research_files/blobstore.py:214`) a `path(key)` returning `BLOB_MOUNT_DIR/key` when set.
7. **Lifecycle prefixes**: `src/documents/service.py:114` `quarantine_key` ->
   `quarantine/tenants/{tenant}/{document}/raw`; `src/research_files/blobstore.py:337` `part_key`
   and `:342` `upload_prefix` -> `uploads/tenants/{tenant}/{upload}/...`, so the bucket's 7-day
   and 3-day lifecycle rules apply (they cannot match a middle segment). Tenant offboarding must
   then also delete those two per-tenant prefixes.
8. **Nightly job database**: `src/account_intelligence/worker/__main__.py:26` `_check` uses
   `SessionLocal` without binding the Cloud SQL connector engine, so in staging/production the
   `spend-invariants` job connects to the unbound URL and exits 3. Bind it as
   `api/main.py` lifespan does (`cloud_sql_engine`, `bind_engine`) when `APP_ENV` is a cloud
   profile.
9. **Stalled-runs alert**: `src/account_intelligence/api/main.py:227`: log
   `runs.stalled` (with a count) when `_mark_stalled()` returns rows; the alert listens for that
   event.
10. **app_rw grant (L16)**: a migration that grants `app_rw` to each name in `APP_RW_IAM_USERS`
    (comma separated, set on the migrate job; skip names that do not exist yet), and nothing
    else; register the variable (component migrate).
11. **Migrator is not a superuser (M1)**: on Cloud SQL the migrator is a `cloudsqlsuperuser`
    member with CREATEROLE (no SUPERUSER, no BYPASSRLS). Migration 0070 must not assume
    superuser; creating the NOLOGIN owners and `ALTER FUNCTION ... OWNER TO` them works because
    PG16 gives a CREATEROLE user ADMIN on roles it creates. `CREATE EXTENSION vector` works for
    that member.
12. **Phase 5 routes**: the paused ticks call `/tasks/sweep`, `/tasks/retention`,
    `/tasks/alert-digest`, `/tasks/audit-verify`; use these names or tell me (or the lead) the
    real ones.
13. **Readiness view**: the api's `/readyz` components (`api/routes/health.py`) show providers
    "unconfigured" in the cloud because provider keys are mounted on the worker only (least
    privilege). Report provider state from the worker or by secret metadata, or label it.

### To Agent 5 (ci-and-images) and the lead (`scripts/deploy_gcp.sh`, unowned)

1. `scripts/deploy_gcp.sh:6-7`: required settings: drop `CLOUD_RUN_RUN_JOB`, add
   `CLOUD_RUN_WORKER_SERVICE` and `CLOUD_RUN_SPEND_JOB`.
2. `scripts/deploy_gcp.sh:91-92`: replace the `run` job update with
   `gcloud run services update "$CLOUD_RUN_WORKER_SERVICE" --image "${digests[worker]}"` and
   `gcloud run jobs update "$CLOUD_RUN_SPEND_JOB" --image "${digests[worker]}"`.
3. `scripts/deploy_gcp.sh:98`: the readiness loop also checks `$CLOUD_RUN_WORKER_SERVICE`.
4. `.github/workflows/deploy-staging.yml:40-43` and `deploy-prod.yml`: pass
   `CLOUD_RUN_WORKER_SERVICE` and `CLOUD_RUN_SPEND_JOB` from environment variables, drop
   `CLOUD_RUN_RUN_JOB`.
5. CI infra job: also run the module tests (`terraform test` in
   `infra/modules/{cloud_run,gcs,cloud_sql,scheduler,tasks,iam,secret_manager}`), tflint with
   `infra/.tflint.hcl` (`--init`, then `--recursive`) and checkov (`-d infra --framework
   terraform`); all pass today.
6. `docker/worker.Dockerfile`: nothing required (Cloud Run overrides the entry point with
   uvicorn on 8080); `EXPOSE 8080` would document it.

### To the lead

1. `docs/DEPLOY.md` sections 2, 3 and 6 describe the worker as a Cloud Run Job and a
   five-account IAM set: point them at docs/deploy/RUNBOOK.md and ARCHITECTURE.md.
2. `docs/runbooks/deploy.md`, `secrets.md` (secret list), `database.md` (IAM grant for three
   users, migrator not a superuser) and `bootstrap.md` (asia-south1 example) are older than this
   design; RUNBOOK.md supersedes them for the cloud.
3. `docs/ENVIRONMENT.md` regenerates once Agent 4 registers `CSRF_KEY_SECRET`,
   `TASKS_QUEUE_DOCUMENTS`, `TASKS_QUEUE_JOBS`, `BLOB_MOUNT_DIR`, `APP_RW_IAM_USERS`.
4. `Makefile` `infra-*` targets could call the same container commands as section 2.

## 7. Commits

See `git log feat/replica..agent/gcp-infra` (four commits: observability, identities and edge,
services and data, docs).
