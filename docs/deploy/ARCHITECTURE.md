# Google Cloud architecture (staging and production)

What `infra/` builds, why, and how big. Nothing here has been applied: the owner runs Terraform
when they say "deploy" (docs/deploy/RUNBOOK.md). Every price below is an **estimate** from public
list prices as known in October 2026; check the Google Cloud pricing calculator before relying on
it.

## 1. The picture

```
 browser ──HTTPS──> global external load balancer (managed certificate, TLS 1.2+, HTTP->HTTPS)
                     │  Cloud Armor: OWASP CRS 3.3, log4j, per-IP limits (stricter on sign-in)
                     ▼
               Cloud Run "web" (Next.js; ingress: internal + load balancer)
                     │ /v1 proxied server side over direct VPC egress
                     ▼
               Cloud Run "api" (FastAPI; ingress: internal only)
                     │ enqueue (OIDC as the tasks identity)
                     ▼
     Cloud Tasks: runs │ documents │ jobs        Cloud Scheduler (OIDC ticks, Run Admin API)
                     ▼                                   │
               Cloud Run "worker" (same app, SERVICE_ROLE=worker; ingress internal;
                     │  invoker: tasks identity only; clamd sidecar; bucket mounted read only)
                     │
   ┌─────────────────┼───────────────────────┬────────────────────────┐
   ▼                 ▼                       ▼                        ▼
 Cloud SQL PG16   Memorystore Redis     Cloud Storage bucket     Secret Manager, KMS
 (private IP,     (private service      (uniform, no public,     (one reader list per
  IAM auth)        access, TLS)          CMEK, CORS web only)      secret)
                                          ▲
 browser ──resumable upload chunks (PUT)──┘ straight to storage, never through the api

 Cloud Run Jobs: migrate (deploy step), spend-invariants (nightly)
 Egress to providers and model APIs: Cloud NAT with one reserved address (output nat_ip)
```

Staging and production are the same `infra/stack` called from `infra/envs/staging` and
`infra/envs/prod`, with separate variables (`terraform.tfvars`, not committed) and separate state
(a GCS backend the owner creates, `prefix=staging` or `prefix=prod`), in separate projects
(ADR-0020).

## 2. Decisions

### What the existing Terraform had already chosen (kept)

- One project per environment, Workload Identity Federation for CI (no key files), images pinned
  by digest, Terraform owns everything but the image digest (CI rolls it).
- Cloud SQL PostgreSQL 16, private IP, CMEK, IAM database authentication; one CMEK key ring per
  environment; the BYOK key for tenant model keys.
- api reached privately by web; web public only through the load balancer; Cloud Armor;
  HTTP-to-HTTPS redirect.
- Secret Manager containers only (values out of band), each with its own reader list.
- Data bucket with versioning **and** soft delete (the brief allowed either; a deleted object is
  recoverable for up to 7 days as a noncurrent version plus 7 days soft deleted, which the
  privacy policy should mention).
- Cloud Tasks retry counts kept low for runs (the graph's RetryPolicy owns provider retries).

### What changed

| Before | Now | Why |
| --- | --- | --- |
| Cloud Tasks called the **api**; a Cloud Run Job `run` per research run (`RUN_JOB`, never read by the code) | A private Cloud Run service **worker** serves `/tasks/*`; the `run` job is gone | ADR-0018 describes a private worker service; slices are 240 s requests; the api no longer needs run.developer on a job |
| One queue | `runs`, `documents`, `jobs` | Document parsing must not starve run slices; separate rate and retry settings |
| Generated alerts on metrics that do not exist yet | Gated by `workload_alerts_enabled`; hand-written alerts on Google metrics | Monitoring refuses an alert on an unknown metric, so apply would have failed |
| `REDIS_URL` on port 6379 | Port 6378 with the instance CA mounted as a file | Memorystore with in-transit encryption listens on 6378 and signs with its own CA |
| Cloud SQL edition implicit | `edition = ENTERPRISE` | PostgreSQL 16 defaults to Enterprise Plus, which refuses `db-custom-*` tiers |
| No CSRF key secret | `csrf-signing-key` (api only) | Audit M6 |
| Sign-in rate rule: login, resend, verify, enroll | Also reset, reset request, invite accept, SSO discover and start; staff sign-in rule | Audit M3 |

### How the api is reached: internal ingress from web, not the load balancer

The browser never calls the api: the web server proxies `/v1` (`web/app/v1/[[...path]]`,
`API_ORIGIN`). So the api has `INGRESS_TRAFFIC_INTERNAL_ONLY` and the load balancer has one
backend, web. Reasons:

1. One public surface. Cloud Armor, the certificate, the CSP and the same-origin checks sit in
   front of everything; the api has no internet ingress at all, so a route mistake cannot be
   reached from outside.
2. The session cookie stays first party on one origin; no CORS on the api.
3. One certificate, one backend service, one serverless NEG.

Costs of this choice: the web server carries `/v1` request bodies (uploads do not: they go
straight to storage), and **the api sees the web server, not the browser, as its TCP peer.** The
api therefore skips its own per-IP bucket when Redis is configured (`identity/rate_limit.py`), and
**Cloud Armor is the per-IP limit in the cloud** (stricter rule for sign-in, reset, invite and SSO
routes: 30 per minute per IP; staff sign-in: 60; everything else: `rate_limit_per_minute`,
default 1200, build assets exempt). The per-account and per-token buckets in the api still apply.

The api keeps `allUsers` as invoker: with internal ingress only VPC traffic reaches it, and the
web server forwards the user's cookie rather than a Google ID token. Every business request is
authenticated by the api itself.

### The worker

- Ingress internal; Cloud Tasks and Cloud Scheduler count as internal sources.
- `run.invoker` for the **tasks** identity only; no `allUsers`. The application checks again that
  the OIDC token is Google-signed, has the audience `ai-<env>-worker-tasks` (a Cloud Run custom
  audience) and the tasks identity's email (`platform/gcp/adapters.verify_task_token`).
- Image: the worker image, started as a service with `uvicorn account_intelligence.api.main:app`
  and `SERVICE_ROLE=worker` (the same app; the sweeper runs only in the worker).
- CPU always allocated (`cpu_idle = false`): the orphan and stall sweeper is a background loop.
  With `min = 0` (staging) it runs only while an instance is up; production keeps one instance.
  A scheduled `/tasks/sweep` tick is created paused for FIX_PROMPT 5.4.
- clamd sidecar (`DOCUMENT_SCANNER=clamd`, `CLAMD_HOST=127.0.0.1`): staging and production
  accept no other scanner (fail closed), so without it every upload is rejected. The main
  container starts only after clamd answers on 3310.
- The data bucket is mounted read only at `/mnt/blobs` (Cloud Storage FUSE, gen2). Today the
  document service copies the whole object to a temporary file for the sandbox, and on Cloud Run
  temporary files live in memory: a large file would exhaust the instance. The mount lets the
  sandbox read the object in place once the handoff to Agent 4 lands (`BLOB_MOUNT_DIR`). Until
  then, the largest document a worker can parse is bounded by its free memory (about 6 GiB with
  nothing else running): **do not raise `platform_defaults.max_file_bytes` above 2 GiB in the
  cloud until the handoff lands.**

### Database identities (audit L16 and M1)

- Runtime: the api, worker and nightly-job service accounts connect as Cloud SQL **IAM users**
  (`CLOUD_IAM_SERVICE_ACCOUNT`, through the Cloud SQL connector, no password). Each must be a
  member of `app_rw` and **nothing else**: not `cloudsqlsuperuser`, no `BYPASSRLS`, no
  `CREATEROLE`/`CREATEDB`, and owner of no table, view or function. They never run DDL. The
  runbook's post-deploy check proves it with a query; the migrate job receives the list
  (`APP_RW_IAM_USERS`) so a migration can make the grant (handoff).
- The run checkpointer connects with `CHECKPOINT_DATABASE_URL` (the `app_rw` password URL with
  `sslmode=require`), so `app_rw` itself also logs in; the RLS catalog check covers it.
- Migrator: a separate built-in Cloud SQL user (`migrator`), created by the owner once, whose URL
  only the migrate job's account can read. On Cloud SQL it is **not a superuser**: built-in users
  are members of `cloudsqlsuperuser` (CREATEROLE, CREATEDB, `CREATE EXTENSION vector`), without
  SUPERUSER, REPLICATION or BYPASSRLS. Consequences: migrations must not assume superuser; the
  SECURITY DEFINER sweep functions need the NOLOGIN owner roles of audit M1, or they return no
  rows under FORCE RLS. Since PostgreSQL 16 a CREATEROLE user holds ADMIN on the roles it creates,
  so the migrator can create those owners and `ALTER FUNCTION ... OWNER TO` them.

## 3. Sizing and its arithmetic

Inputs from the code and config:

| Input | Value | Source |
| --- | --- | --- |
| Run slice length | 240 s, at most 60 slices | `config/workflow_v1.yaml` (`slice_seconds`, `max_slices`) |
| Document part sandbox | 1536 MiB, 1800 s CPU, 1800 s wall clock per part | `config/research_input_modes.yaml` (`sandbox`) |
| Parallel parts per client | 4 | `parsing.max_parallel_parts` |
| Browser upload chunk | 32 MiB, resumable, straight to storage | `upload.chunk_bytes` |
| Cloud Tasks dispatch deadline | default 10 min, maximum 30 min (set per task) | service limit, VERIFY(V-TASKS-1) |
| Cloud Run request timeout | maximum 60 min | service limit |

### Per instance

| Service | vCPU | Memory | Concurrency | Timeout | CPU between requests | Why |
| --- | --- | --- | --- | --- | --- | --- |
| web | 1 | 512 MiB | 40 | 300 s | throttled | Next.js server; 300 s so Ask streams through the proxy are not cut |
| api | 1 | 1 GiB | 40 | 300 s | throttled | I/O bound; chat streams; Argon2id hashes are 64 MiB each (audit L7) |
| worker | 4 | 8 GiB | 4 | 3600 s | always on | 4 sandbox children x 1536 MiB = 6 GiB, plus the app (about 0.6 GiB) and headroom = 8 GiB; one core per parser so the 1800 s CPU budget is not stretched by sharing; 3600 s because one document request reads parts in a loop |
| worker clamd sidecar | 1 | 4 GiB | n/a | n/a | always on | ClamAV keeps signatures in memory (about 1.5 GiB) and doubles during a reload; the signature files also live in memory |
| job spend-invariants | 1 | 1 GiB | n/a | 1800 s | n/a | one pass over the ledger |
| job migrate | 1 | 1 GiB | n/a | 1800 s | n/a | `alembic upgrade head`; retries 0 |

A run slice (240 s) fits the 10-minute default dispatch deadline with margin for checkpoint
writes. A document request does not fit the 30-minute maximum when one request reads several
parts or one part runs the full 1800 s: Cloud Tasks then records a failure and retries while the
request is still running (the part lease turns the retry into a "busy" 503, so nothing is read
twice, but the attempt count climbs). Handoff to Agent 4: one part per request, and the documents
queue's tasks created with `dispatch_deadline = 1800s`.

### Instances and queues

| Environment | api min/max | web min/max | worker min/max |
| --- | --- | --- | --- |
| staging | 0 / 3 | 0 / 3 | 0 / 2 |
| production | 1 / 10 | 1 / 10 | 1 / 8 |

Queues never dispatch more than the worker can hold at its maximum: worker max x concurrency 4 =
runs 2 + documents 1 + jobs 1 per instance.

| Queue | Rate | Concurrent (staging / prod) | Attempts | Backoff |
| --- | --- | --- | --- | --- |
| runs | 5/s | 4 / 16 | 5 | 10 s to 300 s, 4 doublings |
| documents | 2/s | 2 / 8 | 10 ("busy" is a normal answer) | 30 s to 600 s |
| jobs | 5/s | 2 / 8 | 5 | 10 s to 300 s |

Subnet: direct VPC egress takes 2 addresses per instance, 4 while a revision rolls out.
Production at every maximum: (10 + 10 + 8 + 2 jobs) x 4 = 120 addresses, inside the /24 (251).

### Database connections

Assumed per process (the stack sets `DB_POOL_SIZE=5`, `DB_MAX_OVERFLOW=5`): 10 for the engine,
plus one checkpointer connection per in-flight run slice (worker concurrency 4 at most).

| | staging | production |
| --- | --- | --- |
| api: max x 10 | 3 x 10 = 30 | 10 x 10 = 100 |
| worker: max x (10 + 4) | 2 x 14 = 28 | 8 x 14 = 112 |
| jobs (migrate, nightly) | 2 x 5 = 10 | 2 x 5 = 10 |
| operators, Query Insights, maintenance | 15 | 15 |
| **total** | **83** | **237** |
| `max_connections` (variable) | 200 | 400 |
| alert at 80 percent | 160 | 320 |

Today the Cloud SQL engine (`platform/gcp/adapters.cloud_sql_engine`) ignores `DB_POOL_SIZE` and
uses SQLAlchemy's default 5 + 10 = 15 per process (handoff to Agent 4). Even then: staging
3 x 15 + 2 x 19 + 10 + 15 = 108 < 200, production 10 x 15 + 8 x 19 + 10 + 15 = 327 < 400 (but
above the 320 alert at every maximum at once). The tiers carry these comfortably:
`db-custom-1-3840` (staging) and `db-custom-2-7680` (production) at a few MiB per idle
connection.

## 4. Security summary

| Identity | Project roles | Resource grants |
| --- | --- | --- |
| api | cloudsql.client, cloudsql.instanceUser, cloudtasks.enqueuer, log/metric/trace writers, aiplatform.user (Vertex only) | secrets: checkpoint URL, anthropic, moonshot, csrf-signing-key, mail and drive client secrets, Redis CA; bucket objectUser; BYOK encrypt and decrypt; actAs tasks |
| worker | same as api | secrets: checkpoint URL, anthropic, moonshot, provider keys, mail and drive client secrets, Redis CA; bucket objectUser; archive objectCreator; actAs tasks |
| job (nightly) | cloudsql.client, cloudsql.instanceUser, log and metric writers | secret: checkpoint URL |
| web | log and metric writers | secret: staff OAuth client secret |
| migrate | cloudsql.client, cloudsql.instanceUser, log writer | secrets: migrator URL, app_rw password |
| tasks | none | run.invoker on the worker |
| scheduler | none | run.developer on the spend-invariants job |
| deployer (CI, WIF) | run.developer, artifactregistry.writer | actAs api, worker, job, web, migrate; run.developer on the migrate job |

No workload uses the default compute service account (disabled by Terraform). The WIF provider
accepts only this repository's deploy ref from the named GitHub environment (`sub`, `repository`,
`ref` and `environment` all pinned).

Network: Cloud SQL and Memorystore have private addresses only; a deny-all ingress firewall rule;
Private Google Access for Google APIs; all other egress through Cloud NAT with one reserved
address. Memorystore AUTH is off on purpose (its AUTH string would be stored in Terraform state);
only this project's Cloud Run services are in the VPC.

Object access to the buckets is recorded by Cloud Audit Logs (Admin Activity always; turn on
Data Access logs for Cloud Storage if the security owner wants every read, at extra log cost).

## 5. Monitoring

Uptime checks on `https://DOMAIN/healthz` (web) and `https://DOMAIN/readyz` (web asks the api's
`/readyz` privately, so this is the api's check). Alerts (page unless noted): either check down;
5xx above 0.1/s on web or api; p95 latency above 3 s (ticket); a queue above 100 waiting tasks
for 15 minutes; database CPU above 80 percent, memory above 90 percent, connections above 80
percent of `max_connections`, disk above 80 percent (ticket); a failed migrate execution; the
nightly isolation check failing (`isolation_check_failed`), any failed Cloud Scheduler tick
(a check that did not run is a failed check); runs marked stalled (`runs.stalled`, a log event
Agent 4 adds), runs ended worker_lost (ticket); sweeper failures for 30 minutes. Dashboard:
"ai-ENV platform". Budget: alerts at 50, 90 and 100 percent of `monthly_budget_usd`, and at a
forecast of 100 percent. The application's own alerts (`config/telemetry/alerts.yaml`) are
created when `workload_alerts_enabled = true`, after the OTLP exporter ships (V-OBS-1).

## 6. Estimated monthly cost at low traffic (estimates)

Assumptions: us-central1 list prices (Tier 1), October 2026; no free tier counted; low traffic
(staging: about 40 worker-hours a month; production: a few hundred runs a month, worker kept
warm by `min = 1`); 20 GB database disk; 50 GB (staging) and 200 GB (production) in the bucket;
logs inside the 50 GiB free allotment. **Model and data-provider spend are not included**: they
are usage based and capped in the application per run and per tenant.

| Item | Staging | Production, HA on | Production, HA off |
| --- | --- | --- | --- |
| Cloud Run web + api (production: 1 warm instance each, idle rate) | 5 | 45 | 45 |
| Cloud Run worker (4 vCPU 8 GiB + clamd 1 vCPU 4 GiB, CPU always on) | 16 | 300 | 300 |
| Cloud SQL (1 vCPU 3.75 GB / 2 vCPU 7.5 GB, SSD, backups) | 54 | 210 | 107 |
| Memorystore 1 GB (Basic / Standard HA) | 36 | 47 | 36 |
| Load balancer (2 forwarding rules) and data | 19 | 20 | 20 |
| Cloud Armor (policy, about 10 rules, requests) | 15 | 16 | 16 |
| Cloud NAT and 2 reserved addresses | 10 | 15 | 15 |
| Storage, registry, secrets, KMS, Scheduler, Tasks, flow logs | 6 | 12 | 12 |
| **Total (USD per month, estimate)** | **about 160** | **about 665** | **about 550** |

- europe-west2 (London): Cloud Run, Cloud SQL and Memorystore list prices there are roughly 15 to
  25 percent higher; expect about 190 (staging) and 780 / 650 (production).
- The warm worker is the largest line. Setting production `scaling.worker.min = 0` saves about
  270 a month, at the cost of cold starts (clamd needs a minute or two to load) and a sweeper that
  runs only while work is in flight; do it once the scheduled `/tasks/sweep` tick exists.
- Staging can be stopped between test sessions: scale-to-zero already holds for Cloud Run; the
  database and Redis are the fixed part (about 90 a month).
