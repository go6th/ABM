# Account Intelligence roadmap

This roadmap follows the skill's `references/11-build-plan.md`. The project is currently assembling the foundation; it does not claim the Phase 0 exit gate or start product work packages.

## Phase 0: Prove (weeks 1 to 3)

| Work package | Scope and exit evidence |
|---|---|
| 0.1 | Isolation spike: forced RLS, non-owner role, pooled connections, checkpoint prefix policy, partitioned vectors, and zero cross-tenant reads under 100 concurrent requests / 5 connections. |
| 0.2 | Durability spike: resume a two-node graph after five kills; test the provider-response/ledger gap and concurrent workers. Resolve the exactly-once versus at-most-once repeat guarantee. |
| 0.3 | Prompt-cache spike: ten realistic runs, at least 70% cache reads from call two, stable across six-minute slices. |
| 0.4 | Broker economics: 20 real accounts at standard depth, within the approved cap and at least 80% expected-attribute coverage. Requires human approval before any spend or real tenant data. |
| 0.5 | Autoscaling spike: 50 synthetic runs with stub brokers; schedule when the EKS/Karpenter baseline exists. |

**Exit:** spikes 1 to 4 meet their criteria or the design is amended and recorded. These measurements require human-approved infrastructure, vendor terms, and spend; do not run without authorization.

## Phase 1: MVP concierge (weeks 4 to 11)

| Work package | Scope |
|---|---|
| 1.1 | Repository, Python tooling, CI skeleton, and Terraform baseline. |
| 1.2 | Core schema, RLS, roles, migrations, and checkpointer setup between core and policy migrations. |
| 1.3 | Tenant-scoped DB access, JWT authentication, and RBAC. |
| 1.4 | Tool-call ledger, budget guard, recorded adapters, and broker interfaces. |
| 1.5 | Research graph, quarantined extraction, verification, time-sliced runner, Inngest loop, and reconciler. |
| 1.6 | Grading, normalization, stable source IDs, property tests, and 100% grading coverage. |
| 1.7 | Wallet reserve/settle/refund invariants and reconciliation. |
| 1.8 | Deliverables and client-safe quality gate. |
| 1.9 | Operator console and evidence UI. |
| 1.10 | Golden set, evaluation harness, adversarial corpus, and CI eval gates. |
| 1.11 | OpenTelemetry, log allow-list, dashboards, and SLO alerts. |

**Exit:** operators complete 100 accounts end to end, grade calibration is measured, and isolation tests have zero failures.

## Phase 2: V1 self-serve (weeks 12 to 19)

Onboarding/SSO and RBAC; billing and top-ups; scheduled probes and alerts; rate limits and concurrency controls; warm standby and failover rehearsal; SOC 2 evidence; and staging chaos/canary releases.

**Exit:** ten paying tenants, SLOs met for 30 days, and DR rehearsal passed.

## Phase 3: Scale and Phase B (weeks 20 to 28)

Private-context connectors and quarantine, redaction, chunking and retrieval, internal view with two-output enforcement, and DSAR flows.

**Exit:** zero leakage canaries, retrieval targets reported, and SOC 2 Type I issued or scheduled.

## Known early gaps and deferred decisions

- **First Phase 0 blocker:** the starter's billing ledger permits duplicate paid calls during concurrent execution and the crash window. Add a per-run lease and intent/reconciliation design; do not claim exactly-once billing yet.
- **First Phase 1 blocker:** an idempotency-key replay is not checked against the request body; a different domain with the same key currently replays the earlier run.
- D-01 to D-11 remain open until answered in the source skill's decision register. They block the dependent work packages listed in `references/12-open-decisions-and-verification.md`.
- GitHub Actions in `.github/workflows/ci.yml` are pinned to verified commit SHAs; verify and refresh them when updating actions.
