# 11 Build plan: phases, work packages, tests and exit criteria

Phases and weeks follow ARCH 17 (conflict C5). The skill's job in each work package: **build the smallest thing that meets the "done when" line, run the named tests, record evidence in `BUILD_LOG.md`, then stop at the exit gate and report.** Never start the next phase until the current phase's exit criteria are met or the person explicitly waives one in writing.

## Ordering rules (ARCH 17.1)
1. Isolation and durability first: every later feature inherits them; retrofitting either is far more expensive.
2. The billing ledger precedes self-serve: free runs with real broker costs are the fastest way to lose money.
3. The evaluation harness is built **alongside** the first graph.
4. Phase B (private context) is last, on a platform whose isolation has been exercised in production.

## Phase 0: Prove (weeks 1 to 3). Output: spike reports, not product code
Put spike code under `spikes/` and a one-page result per spike under `docs/spikes/`. If a spike fails, **amend the design (record it in `docs/decisions/`) before any feature work**.
| # | Spike | Pass criterion | If it fails |
| --- | --- | --- | --- |
| 1 | Isolation: forced RLS, non-owner role, pooled connections, checkpoint prefix policy, partitioned vectors with canary vectors | 100 concurrent mixed-tenant requests over a 5-connection pool give **zero** cross-tenant rows; unset context returns zero rows | Revisit tenancy tier defaults before any feature work |
| 2 | Durability: two-node graph; kill the process mid fan-out repeatedly; resume via slices | Run completes after 5 random kills; ledger shows each paid call exactly once | Evaluate Temporal for the outer loop (conflict C1) |
| 3 | Prompt caching: run the baseline prompt 10 times in a loop with realistic tools | Cache-read ratio >= 70% from the second call; no invalidation across slices of 6 minutes | Adopt the longer cache lifetime or restructure the prefix |
| 4 | Broker economics: 20 real accounts at standard depth | Cost per run within the planned cap; coverage of expected attributes >= 80% | Reprice or narrow the broker set |
| 5 | Autoscaling (listed in ARCH 19.2; ARCH 17 says "four spikes"): burst of 50 simultaneous runs against stub brokers | Pods scheduled within a minute; consolidation after the burst; no run lost on spot reclamation | Adjust pool sizing and disruption budgets |

Start from `assets/starter/` (its tests already encode spike 1 and parts of spike 2). Notes the spike writer must apply (details in `13-starter-code-and-known-defects.md`):
- **Spike 1**: `test_100_concurrent_mixed_tenant_requests_over_5_connections` and `test_canary_vector_does_not_cross_tenants` already exist in the starter suite; run them against Aurora, not only a laptop database. It must also prove `public_reader` cannot read private facts (defect 2).
- **Spike 2** must kill **inside the window between the provider response and the ledger insert** and must also run **two workers on one run** (a retried slice while the first is still running). The starter `ledgered_call` has no lease, so concurrent duplicates both pay (defect 3). Also settle which guarantee the product promises: the spike criterion says "each paid call exactly once", while ARCH 5.5 promises at most one repeat per crash.
- Spike 5 needs the EKS and Karpenter baseline, so schedule it at the start of Phase 1 (my sequencing suggestion).
- Spike 4 is the first measurement of the PRD's "about one working day per account" against ARCH's p50 < 10 min target (conflict C7).

**Exit:** spikes 1 to 4 meet their criteria, or the design is amended.

## Phase 1: MVP, concierge (weeks 4 to 11). Operators run it, clients see results
| WP | Build | Done when (tests) |
| --- | --- | --- |
| 1.1 | Repo, `pyproject`, CI skeleton, Terraform baseline (network, EKS, Aurora, Karpenter, S3+KMS, IAM) | CI green; `terraform plan` clean; separate dev and staging accounts |
| 1.2 | Start from `assets/starter/migrations/` (0001 core tables, 0002 roles, `app_tenant()`, RLS) and extend 0001 with sources, coverage_rows, people, job_postings, tech_detections, alerts, audit_log; checkpointer `setup()` runs between 0001 and 0002 | `scripts/lint_migrations.py` passes; **`pytest tests/isolation` green**; `scripts/check_rls_catalog.sql` returns no rows; app_rw is NOBYPASSRLS and not the owner |
| 1.3 | `tenant_txn`, JWT auth, RBAC, tenant claim never from the body (start from `assets/starter/src/`) | Unit and isolation tests; revoked member loses access within ~60 s |
| 1.4 | Tool ledger + budget guard + adapters behind one interface (`assets/routing.yaml`); cassettes | Contract tests; kill-test shows no duplicate ledgered call; budget breach ends in BUDGET_EXCEEDED with partial results |
| 1.5 | Research graph: baseline, plan, fan-out branches, `quarantine_extract` (tool-less), merge, `distill_grade`, `verify`, render nodes; time-sliced runner; Inngest outer loop; reconciler | Resume after kill; stuck-run reconciler; injection fixtures dropped |
| 1.6 | `grading.py` + normalisation + source codes S-0001 | Property tests; 100% coverage; worked examples from `scripts/grading_reference.py` reproduced |
| 1.7 | Wallet: reserve, commit, refund, settlement incl. FAILED and CANCELED paths; nightly invariant job | No negative balance; one settlement per terminal run; replayed request returns the same run |
| 1.8 | Deliverable builders (Summary, Evidence, People, Jobs, Tech, workspace zip) and the **gate service** (PDF render check, orphan citations, banned dashes, stale phrases, excluded names, recalculated workbooks, tracker tally, third-party scan) | Gate blocks a seeded defect; `scan_client_output.py` clean on every export |
| 1.9 | Operator console and evidence UI: run list, status, coverage tracker, evidence viewer, approve budget, download files | Operator completes a run end to end from the UI |
| 1.10 | Eval harness: golden set v1 (50 accounts with capture dates), judge, adversarial corpus, gates | Gates computed in CI; calibration plot produced |
| 1.11 | Observability baseline (OTel, run id = trace id, log allow-list with canary strings, dashboards, SLO burn alerts) | Canary strings never appear in logs; pages routed |

**Exit:** operators run **100 accounts end to end**; grade calibration measured; **zero isolation test failures**.

## Phase 2: V1, self-serve (weeks 12 to 19)
| WP | Build | Done when |
| --- | --- | --- |
| 2.1 | SSO and tenant onboarding; RBAC for admin, seller, viewer | Onboard a synthetic tenant with no operator help |
| 2.2 | Billing and top-ups (payment webhook idempotent on event id), plan limits, depth-tier pricing | Webhook replay test; reserve/commit/refund invariants hold under concurrency (10 parallel runs cannot overspend) |
| 2.3 | Scheduled probes and delta alerts (`assets/alert_rules.yaml`), digest, Slack/email, signed webhooks | Dedupe and debounce tests; alert freshness SLO measured |
| 2.4 | Rate limits, per-tenant concurrency, idempotency everywhere | Load test meets SLOs at 3x forecast |
| 2.5 | Warm standby region, scripted failover runbook, first game-day | DR rehearsal passed; RPO and RTO measured and reported |
| 2.6 | SOC 2 evidence collection, audit log archive with Object Lock, access reviews | Evidence tracker live |
| 2.7 | Chaos experiments in staging; canary releases with auto-rollback | All six chaos pass criteria met |

**Exit:** 10 paying tenants; SLOs met for 30 days; DR rehearsal passed.

## Phase 3: Scale and Phase B (weeks 20 to 28)
| WP | Build | Done when |
| --- | --- | --- |
| 3.1 | Connectors (OAuth, presigned upload), quarantine bucket + virus scan, sandboxed parsers, speech with diarization | Nothing is parsed before the scan passes; parser pod has no egress except its vendor |
| 3.2 | Redaction tiers (Strict, Standard, Minimal), classification, redaction maps | Per-tier tests; original kept, map stored |
| 3.3 | Chunking, embeddings, hybrid retrieval, rerank, top-8 with spans | recall@8, nDCG@10, citation precision reported per tenant sample |
| 3.4 | Internal view, **two-output rule enforcement**, leak detector in the gate | Planted canary documents never reach a public artefact; leakage canaries at zero |
| 3.5 | Deletion cascade (24 h), DSAR flows, crypto-shredding | Deletion test passes end to end |
| 3.6 | Bridge and silo tiers for tenants who need them | Isolation suite runs against each tier |

**Exit:** leakage canaries zero; retrieval recall targets met; SOC 2 Type I issued or scheduled.

## Team (ARCH 17.2)
Six engineers: principal architect/tech lead, 2 Python backend, 1 AI/ML, 1 frontend, 1 platform/DevOps; add a part-time security engineer from Phase 2.

## BUILD_LOG.md format
One entry per work package: date, what was built, tests run and results (paste the command and the summary line), conflicts met and how the register settled them, open decisions touched (D-xx), and [VERIFY] items resolved (V1 to V11) with the evidence. **A work package is not done until its log entry shows passing tests.**
