# 10 Testing, evals, CI/CD, operations and cost (ARCH 10.4, 11 to 13, 15, 16)

## Test layers and gates
| Layer | What | Gate |
| --- | --- | --- |
| Unit | Normalisation, grading maths, budget guard, ledger keys, state migrations (pytest + hypothesis) | 80% line coverage, **100% on grading and billing** |
| Integration | API with real Postgres and Redis, RLS policies, ledger idempotency (Testcontainers, real migrations) | All pass |
| **Isolation** | Cross-tenant read, write, unset context, pooled leak, checkpoint prefix, partition canaries, **public_reader cannot read private rows** | **Zero tolerance; hard CI gate** |
| Contract | Broker and provider adapters: recorded cassettes plus nightly live smoke with spend caps | No schema drift |
| Agent evals | Quality, calibration, injection, leakage, cost (golden set, adversarial corpus, judge) | See gates below |
| End to end | Sign-up to brief to alert (Playwright against staging with fixtures) | Critical paths green |
| Load | k6 for API; synthetic run injector with stub brokers | Meets SLOs at 3x forecast |
| Chaos | Pod kill mid-slice, node loss, Redis loss, workflow-engine outage, DB failover | No lost run, no duplicate paid call |
| Security | DAST, dependency audit, injection red-team, RLS bypass attempts | No high findings open |

Test data policy: synthetic tenants and generated documents only; production data never enters lower environments; planted canary strings and vectors in every test tenant.

## Eval gates (initial values; tune on data)
Precision of grade A >= 0.95 and B >= 0.85 · coverage of expected attributes >= 0.80 · citation validity >= 0.97 (the cited span must support the claim) · grade calibration error <= 0.05 · injection and leakage suite 100% (any failure blocks release) · cost per run within 10% of baseline · p95 duration within 15% of baseline. A candidate is blocked if any gate fails or a paired test shows a statistically significant drop on a primary metric.
Golden set: 50 accounts at MVP growing to 200, stratified by size, industry and region; every label has a **capture date** and is re-verified quarterly (real-world facts change, and a stale label punishes a correct agent). Judge model differs from the generator and is calibrated against human labels on a sample. Compare candidate and baseline on the same accounts with confidence intervals. Maintain an adversarial corpus of injection pages and poisoned documents (100% pass for tool misuse and leakage canaries). Shadow-grade a small share of live runs; track drift weekly. The eval harness is built **alongside the first graph**.

## Chaos experiments (each with a pass criterion)
Kill worker mid-slice (run completes; no duplicate vendor call for ledgered keys) · lose the workflow engine for 10 min (reconciler re-sends; no run stuck beyond 5 min after recovery) · database failover (slices retry and resume; no corrupt checkpoint) · spot reclamation burst (runs resume; p95 within 2x target) · poisoned page (fact dropped or sanitised; no tool misuse; alert raised) · cache invalidation canary (cache-ratio alert within 1 h).

## CI/CD pipeline
Pull request: lint, types, unit, **migration lint** (`scripts/lint_migrations.py`), secret scan → **isolation suite** → security (SAST, dependency audit, container scan, SBOM; block on high or critical) → build (reproducible image, **cosign signature**, push by digest) → **eval gate on main** → deploy staging (smoke tests with recorded broker fixtures) → canary prod 5, 25, 100 percent with automated analysis and auto-rollback (Argo Rollouts).
- Promotion dev → staging → prod across **separate AWS accounts with the same artifact digest**; only configuration differs.
- **Agent logic release**: each run is **pinned to the graph version it started on** (`account_runs.graph_version`); old and new workers coexist until the old version has no active runs; rollback is a routing change plus a version flag. Prompts and graph code ship as one artefact; feature flags by tenant, plan or percentage.
- Migrations: Alembic, **expand then contract**, backward compatible for one release, run by the `migrator` job before rollout (and the checkpointer `setup()`), never at runtime.
- Supply chain: pin third-party actions and base images by digest (the ARCH CI snippet pins by tag with a comment to pin by SHA in production: **do pin**), signatures verified at admission, SBOM per release.

## Infrastructure essentials (ARCH 11)
EKS node pools: system (2 on-demand), api (on-demand, multi-AZ, PDB max 1 unavailable), agents (**spot first**, 120 s grace, resumable), ingestion (spot), jobs (on-demand, do-not-disrupt). Graceful shutdown on SIGTERM: stop taking slices and exit within the grace period (checkpoint plus ledger make interruption safe). Scale agents on queue age and in-flight runs, not CPU. Network: private subnets, VPC endpoints, default-deny NetworkPolicies, **all outbound fetches through an egress proxy with a domain allow-list and SSRF protection** (block link-local and private ranges, cap redirects, refuse the metadata address); private-document parsers in a separate namespace with no route except to their vendor. Secrets in Secrets Manager mounted as files, workload identity (Pod Identity or IRSA) with one narrow role per workload, **no long-lived cloud keys**. Terraform modules (network, EKS, Aurora, S3+KMS, IAM, observability), remote state with locking, plans reviewed in PRs, nightly drift detection.
DR (starts at V1, not MVP): ap-south-1 Mumbai primary, ap-south-2 Hyderabad warm standby, Aurora Global Database (asynchronous), S3 CRR with replica KMS key, Route 53 failover. **RPO < 60 s and RTO 30 min are targets proven by a quarterly game-day**, not guarantees. Seven-step scripted runbook: declare, promote Aurora, scale standby pools, switch DNS, run the reconciler, re-verify the isolation suite on the new writer, re-point observability. At most a few paid calls may repeat; reconcile the wallet against provider invoices after failover. MVP relies on multi-AZ and tested backups. Aurora Global support in ap-south-2 and inter-region latency are **[VERIFY] V2**.

## Observability and SLOs
OpenTelemetry everywhere; **the run id is the trace id**; a span per node and per tool or model call with tier, model, token counts, cache tokens, cost, outcome. Self-hosted LLM tracing inside the boundary; metrics SaaS gets only scrubbed spans. Logs are structured JSON with a **field allow-list**; prompts, document text and tokens are never logged (planted canary strings verify it). **Tenant and run dimensions live in traces and logs, not on every metric** (cardinality): metrics aggregate by plan tier, region and tool.
SLOs: API availability 99.9% (page on burn rate 14.4x over 1 h and 6x over 6 h) · API p99 < 500 ms · 95% of runs reach COMPLETED or PARTIAL within the p95 target · alert freshness 95% within 24 h · cache-read ratio >= 70% · **isolation violations zero (page on any RLS-denied spike or canary leak)** · checkpoint resume success 99.9%. Every page has a runbook; blameless post-incident reviews; DR game-day and chaos exercise quarterly.

## Cost model (illustrative; recompute from the ledger)
Assumptions in ARCH 16.1 are all tagged illustrative or [VERIFY]. Per-run LLM cost about $1.12 under the stated assumptions; **brokers dominate (about 64 to 70% of total), not tokens.** All-in cost per run $7.04 at 1k runs/month, $5.72 at 10k, $4.68 at 100k; price per run for 70% gross margin $23.48, $19.08, $15.62. Cache-read ratio sensitivity: 0% → +45%, 50% → +15%, 90% → -9% on LLM cost. Highest-value levers: probe-then-escalate refresh, not re-fetching attributes within their half-life, ledger-driven broker price negotiation, cross-tenant sharing of public broker payloads only where licences allow. **Do not quote these numbers to a client as measured**; Phase 0 spike 4 replaces them with real cost on 20 accounts.
