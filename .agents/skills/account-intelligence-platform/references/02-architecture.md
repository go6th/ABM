# 02 Architecture (ARCH chapters 1 to 3, 11, 12)

## Ten decisions
1. **Tenancy:** pooled Postgres with forced RLS by default; bridge and silo tiers for enterprise.
2. **Durability owner:** LangGraph checkpoint is the single source of truth; Inngest only triggers, schedules and loops slices.
3. **Vector store:** pgvector `halfvec(1536)`, hash-partitioned by tenant, HNSW + iterative scan, hybrid with tsvector, rerank.
4. **LLM usage:** tiered routing (planner / extractor / judge) by config; prompt-cache-aware layout.
5. **Untrusted content:** quarantined, tool-less extractor produces schema-validated JSON; the planner never reads raw web text.
6. **Two-output rule:** DB roles + visibility labels + independent leak detector.
7. **Cost control:** tool-call ledger, in-graph budget guard, probe-then-escalate refresh.
8. **Billing:** reserve / commit / refund wallet ledger with atomic balance check.
9. **Regional DR:** Mumbai primary, Hyderabad warm standby, Aurora Global (async), RPO < 60 s and RTO 30 min as *targets* proven by game-day. DR starts at V1, not MVP.
10. **Release safety:** canary with auto-rollback, graph version pinned per run, eval gate in CI.

## Design principles
Isolation by construction (DB enforces tenancy; app code is a second layer). Fail closed (missing tenant context returns zero rows; missing policy config blocks deployment). One owner per concern (durability, retries per failure class, billing truth). Evidence first. Cost is a feature (every paid call ledgered, capped, attributable). Version everything (graphs, prompts, schemas, checkpoint state).

## Containers (primary region, EKS in private subnets)
Web Portal (Next.js) · Core API (FastAPI) · Agent Workers (LangGraph) · Ingestion Workers (Phase B) · Alert Engine · Reconciler / outbox relay (stuck-run recovery, GC, ledger close) · Aurora PostgreSQL (RLS, pgvector, checkpoints; roles `app_rw` and `migrator`) · Redis (rate limits, locks, idempotency cache; **never source of truth**) · S3 + KMS (raw payloads, uploads, artefacts, audit archive with Object Lock). **Workers are stateless;** durable state lives only in Postgres and S3. Ingress: ALB + WAF + mTLS to services.

Node pools: `system` (2 small on-demand across AZs), `api` (on-demand multi-AZ, PDB max 1 unavailable), `agents` (spot first, on-demand fallback, 120 s grace, resumable), `ingestion` (spot), `jobs` (on-demand, do-not-disrupt: migrations, reconciler). Scale agents on **queue age and in-flight runs**, not CPU (workers are I/O bound).

## Network and egress
Private subnets; VPC endpoints (S3, ECR, STS, KMS, Secrets Manager); default-deny NetworkPolicies; **all outbound fetches through an egress proxy with a domain allow-list and SSRF protection** (block link-local and private ranges, cap redirects, refuse the metadata address); Phase B parsers run in a separate namespace with no route except to the document vendor.

## Capacity (Little's law, 10-min mean run, 10x burst for scheduled refresh)
1k runs/month: peak 2 concurrent. 10k: 23. 100k: 231. 1M: 2315. Real limits are broker rate limits, LLM rate limits and Postgres connections, not CPU, so use **per-tenant and per-broker concurrency caps**. Size writer max connections as (API pool x pods) + (worker concurrency x pods) + headroom.

## Non-functional targets
API 99.9% monthly; p99 < 500 ms (run creation returns 202); run p50 < 10 min, p95 < 45 min, hard ceiling 4 h; 95% of runs reach COMPLETED or PARTIAL within p95; no run lost on pod, node or AZ failure and zero duplicate paid calls; zero cross-tenant reads or writes; RPO < 60 s, RTO 30 min; signal-to-alert < 24 h on daily probes; gross margin >= 70% at list price; SOC 2 readiness by month 6.

## Infrastructure as code and environments
Separate AWS accounts: management, security/log archive, shared services, dev, staging, prod; service control policies deny disabling logging and encryption. Terraform modules (network, eks, aurora, s3-kms, iam, observability); remote state with locking; plans reviewed in PRs; nightly drift detection. **Same container digest promoted staging to prod; only configuration differs.** Secrets in Secrets Manager (replicated to standby), mounted as files via External Secrets or CSI (not env vars); EKS Pod Identity or IRSA with one narrow role per workload; no long-lived cloud keys; rotate DB credentials automatically and broker keys on a calendar.

## DR runbook (scripted and rehearsed)
Declare incident, promote secondary Aurora, scale standby pools, switch DNS, run the reconciler to re-send events for queued runs, verify the isolation suite against the new writer, re-point observability. Loss window: ledger and checkpoint rows from the replication-lag window; consequence: a few paid calls may repeat; reconcile the wallet against provider invoices afterwards.

## CI/CD stages
PR: lint, types, unit, **migration lint (FORCE RLS, WITH CHECK, no BYPASSRLS)**, secret scan → isolation suite → security (SAST, dependency audit, container scan, SBOM; block on high/critical) → build (reproducible, signed with cosign, pushed by digest) → **eval gate on main** → deploy staging with smoke tests on recorded broker fixtures → canary prod 5/25/100 with analysis (error rate, p99, run failure ratio, cache-read ratio, cost per run) and auto-rollback. Pin third-party actions and base images by digest; verify signatures at admission.

## Safe release of agent logic
Each run is pinned to the graph version it started on; old and new workers coexist until the old version has no active runs. Rollback is a routing change plus a version flag, not a data migration. Checkpoint schema changes follow expand-and-contract (removing a state field needs one release of dual support). Prompts and graph code ship as one artefact; feature flags by tenant, plan or percentage.
