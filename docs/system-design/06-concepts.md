# 06 · Concepts used in this review

Plain-language definitions, each tied to where the concept shows up in this system. Alphabetical.

| Concept | What it means | Where it appears here |
|---|---|---|
| **At-least-once delivery** | A queue promises a message arrives, but it may arrive more than once (retries, timeouts). Handlers must be safe to repeat. | Cloud Tasks delivers slices, jobs and documents this way; handlers use named tasks, atomic claims and leases (`02-audit.md` E). |
| **Audit log, tamper-evident** | A record of who did what that can show if someone altered it, usually by chaining hashes and keeping a signed copy somewhere the writer cannot change. | `audit_events` hash chain; not keyed and not exported (SD-12). |
| **Backend-for-frontend (BFF)** | A thin server owned by the web app that the browser talks to; it calls private services on the browser's behalf, so those services never face the internet. | `web/app/v1/[[...path]]/route.ts` forwards `/v1` to the private api with a Google ID token. |
| **Backoff** | Waiting longer between each retry so a struggling service can recover. | Cloud Tasks queues: 10 s doubling to 300 s; node retry policies (`config/providers.yaml:10-16`). |
| **Blast radius** | How much breaks when one thing fails or is compromised. | One project for customers and tests (SD-17); worker concurrency 4 means one crash kills 4 slices (SD-25). |
| **Bottleneck** | The first resource to run out as load grows; it sets the system's real limit. | Runs queue (6), provider ceilings, 1-vCPU database (SD-16). |
| **Circuit breaker** | After repeated failures, stop calling a dependency for a while and fail fast, then try again carefully. | Per provider, in memory per instance (SD-26); none on model calls. |
| **Cold start** | Delay when a serverless platform starts a new instance. | Worker scales from 0; large image with LibreOffice and the embedding model. |
| **Concurrency (Cloud Run)** | How many requests one instance handles at the same time. | web/api 40, worker 4 (ADR-0130 says 1). |
| **Connection pool** | A set of open database connections reused across requests; the total across all instances must stay below the database limit. | 5 + 5 per process; ≈75 of 200 at full scale-out (lead 8). |
| **Content Security Policy (CSP)** | A browser rule set that says which scripts, styles and connections a page may use; blocks most injected scripts. | Nonce-based CSP in `web/proxy.ts`. |
| **Correlation id / distributed tracing** | An id carried by a request through every service so its logs and timings can be joined into one story. | `x-request-id` from web to api, lost at Cloud Tasks (SD-27); OpenTelemetry → Cloud Trace. |
| **CSRF (cross-site request forgery)** | Another site making the user's browser send a request with their cookie. Prevented with a token the other site cannot read. | HMAC `X-CSRF-Token` plus same-origin check. |
| **Customer-managed encryption key (CMEK)** | Data at rest encrypted with a key you control in Cloud KMS, so you can rotate or revoke it. | Buckets and secrets use `ai-staging-data`; tenant Anthropic keys use `ai-staging-byok`. |
| **Data-subject request (DSAR)** | A person asking to see or delete data held about them, a legal right in many places. | Table exists, no flow (SD-04). |
| **Dead-letter queue** | Where messages go after they fail every retry, so someone can inspect them instead of losing them. | Cloud Tasks has none; tasks are dropped after max attempts (`02-audit.md` E). |
| **Default deny** | Anything not explicitly allowed is refused. | Route catalog: unknown routes get 403 (`api/guard.py:64-69`). |
| **Disaster recovery (RPO / RTO)** | RPO: how much data you can lose. RTO: how long until you are back. | PITR 7 days in one region; no drill (SD-06). |
| **Drift (infrastructure)** | When the real cloud configuration no longer matches the code that is supposed to describe it. | Services last changed by `gcloud`; plan not run (SD-09). |
| **Egress / NAT** | Outbound traffic; NAT gives it a fixed public address. | Cloud NAT with static IP `136.80.128.242`. |
| **Encryption in transit** | TLS on every network hop. | LB TLS; Cloud SQL `ENCRYPTED_ONLY`; Redis TLS. |
| **Fencing token** | A number that increases each time ownership of a job changes; storage refuses writes carrying an old number, so a paused old owner cannot overwrite the new one. | `run_leases.epoch` fences paid calls but not checkpoint writes (`02-audit.md` E). |
| **Feature flag** | A switch that turns behaviour on or off without a deploy, globally or per tenant. | `RESEARCH_CAPS`, `TOKEN_GUARD`; DB flags `abm_agents`, `crm`, `monitoring`. |
| **Graceful shutdown** | When an instance is told to stop, finish or checkpoint in-flight work before exiting. | 8 s on api and worker (SD-25). |
| **High availability (HA)** | Running a standby copy in another zone so a single failure does not take the service down. | Cloud SQL ZONAL, Redis BASIC: no HA (SD-06, SD-07). |
| **HNSW** | A graph-based index for fast approximate nearest-neighbour search over vectors. | pgvector index on each `chunks` partition. |
| **Idempotency / idempotency key** | Doing the same request twice has the same effect as once; a client sends a unique key so the server can recognise a retry. | `Idempotency-Key` on runs, body `request_id` on research (SD-23). |
| **Infrastructure as code (IaC)** | Cloud resources described in versioned files (Terraform) and created from them. | `infra/stack` and 16 modules. |
| **Ingress (Cloud Run)** | Which networks may send requests to a service: all, internal only, or internal plus a load balancer. | web internal+LB; api and worker internal. |
| **Lease** | Time-limited ownership of a job that must be renewed; if the owner dies, the lease expires and someone else takes over. | `run_leases` row, 1800 s, renewed every 20 s, stale after 90 s. |
| **Least privilege** | Each identity gets only the permissions it needs. | Dedicated SAs; but Editor on default SAs and personal Owners (SD-13, SD-30). |
| **Load balancer** | Accepts traffic on one address and spreads it across backends; also terminates TLS. | Global external Application LB in front of web. |
| **Multi-tenancy** | Many customers share one deployment, each fenced off from the others. | Tenant id on every table, object key and cache key. |
| **Observability** | Being able to see what the system is doing from logs, metrics and traces. | Present; alert routing missing (SD-03). |
| **OIDC token (service-to-service)** | A signed token from Google identifying the calling service account, checked by the receiver. | Cloud Tasks and Scheduler call the worker with one; checked in code. |
| **On-call** | A named person responsible for responding to alerts at a given time. | Runbooks exist; no channel, no rota (SD-03). |
| **PKCE, state, nonce** | Protections in OAuth/OIDC sign-in against stolen codes, forged callbacks and replayed tokens. | All three in `src/identity/oidc.py`. |
| **Point-in-time recovery (PITR)** | Restoring a database to any moment within a window using its transaction log. | 7 days on Cloud SQL. |
| **Private Service Access (PSA)** | A private connection between your VPC and Google-managed services like Cloud SQL and Memorystore. | Range `10.240.0.0`. |
| **Prompt injection** | Text in data the model reads that tries to change what the model does. | Scraped pages and uploads in Ask; mitigated by read-only tools; web search channel open (SD-15). |
| **Rate limiting** | Capping how many requests a client can make in a period, to protect services and quotas. | Cloud Armor per IP; Redis buckets per user and per provider. |
| **Retention policy** | Rules for how long each kind of data is kept and when it is deleted. | Written in `docs/registers/retention.yaml`, not enforced (SD-04). |
| **Row-level security (RLS)** | Database rules that filter which rows a session can see or change, based on settings such as the tenant id. `FORCE` applies it even to the table owner. | 186 tables, `app_tenant()` per transaction. |
| **Runbook** | Step-by-step instructions for handling a known incident. | ~40 files in `docs/runbooks/`. |
| **Sandbox** | Running untrusted work in a restricted process that cannot reach the network or files. | Parser and `run_python` children with seccomp and rlimits. |
| **Security definer function** | A database function that runs with its owner's rights, used to allow one narrow cross-tenant lookup. | `session_tenant`, `idp_connection_for_domain`, support grant resolution. |
| **Serverless NEG** | How a Google load balancer points at a Cloud Run service. Its timeout is fixed at 60 minutes. | `ai-staging-web-neg`. |
| **Service-level objective (SLO)** | A target such as "99.9 % of requests succeed", used to decide when to alert. | `config/telemetry/slos.yaml`; burn alerts off. |
| **Single point of failure** | One component whose failure stops the whole system. | Zonal database, single-node Redis, one region. |
| **Single-flight** | Collapsing identical concurrent calls into one. | Per run + route + args in the connector pipeline. |
| **SSRF (server-side request forgery)** | Tricking a server into fetching an internal address, such as the metadata server that hands out credentials. | URL guard in `connectors/security.py`; gap in Ask `read_web_page` (SD-15). |
| **State machine** | A fixed set of states and allowed moves between them, enforced so nothing reaches an impossible state. | Run statuses in `runs/transitions.py`. |
| **Step-up authentication** | Asking for a fresh strong sign-in before a sensitive action. | `step_up_at` fresh 600 s; gaps SD-21. |
| **Sub-processor** | A third party that processes customer data on your behalf; buyers expect a complete list. | `docs/privacy/subprocessors.md` misses model providers (SD-05). |
| **Sweeper** | A periodic job that finds stuck or abandoned work and fixes or ends it. | `/tasks/sweep` and an in-process loop per worker. |
| **Tick** | A scheduled call that triggers periodic work. | Nine Cloud Scheduler jobs, all to the worker. |
| **Timeout budget** | The time limits on every hop of a request, which must nest (inner shorter than outer). | Slice 1650 s < dispatch 1800 s < Cloud Run 3600 s. |
| **WAF (web application firewall)** | Rules at the edge that drop requests matching known attack patterns. | Cloud Armor OWASP CRS 3.3. |
| **Zero data retention (ZDR)** | A contract term under which an AI provider does not store prompts or outputs. | Not verified for any model (SD-05). |
