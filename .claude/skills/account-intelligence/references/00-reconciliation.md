# 00 Reconciliation register

The platform skill (`../account-intelligence-platform/`) was written from Architecture v2 (ARCH). The production roadmap (ROADMAP, 1 October 2026) came later and the repository has since made decisions of its own. This register settles each disagreement. **Where the platform skill and this register differ, this register wins.** Status is **Settled** (follow it), **Proposed** (follow it, sign-off pending) or **Open** (ask the person before building on it).

| ID | Topic | Platform skill (ARCH) | Roadmap and repo | Status and default |
| --- | --- | --- | --- | --- |
| R-01 | **Tenancy timing** | Rule 1, C3: `tenant_id NOT NULL` and forced RLS on every tenant table from the first migration; "never ship a single-tenant schema for now" | ROADMAP puts tenancy and RLS in Phase 3. Repo: `intel.accounts.tenant_id` NOT NULL with a default tenant; `intel.runs` has no `tenant_id`; no RLS anywhere (`scripts/check_tenancy.py` reports both) | **Open.** Ask before adding the next table. Recommended: option A below |
| R-02 | Workflow engine | LangGraph checkpoint is the durability source of truth; Inngest triggers and loops time slices | ROADMAP s6 recommends Temporal, agent steps as activities, LangGraph only inside a step. `docs/decision_log.md` D1. Temporal hello-world built | **Proposed: Temporal.** Use platform ref 04 for principles (one retry owner per failure class, ledgered paid calls, budget guard inside the run), not for Inngest or slice mechanics |
| R-03 | Phases and work packages | ARCH: 0 Prove (spikes), 1 MVP WP 1.1 to 1.11, 2 V1, 3 Scale and Phase B | ROADMAP: 0 Foundations, 1 Core pipeline, 2 Speed and quality, 3 Multi-tenant product, 4 Automation and signals | **Settled: ROADMAP phases.** Platform ref 11 is a checklist of engineering detail; mapping below |
| R-04 | Cloud and hosting | AWS, EKS, Aurora, Mumbai primary, Hyderabad standby | ROADMAP s16: cloud and region open. Repo Terraform is cloud-neutral (`infra/`) | **Open (product and tech lead).** ARCH's AWS layout is one candidate. Keep code cloud-neutral |
| R-05 | Grade vocabulary | Product grades plus engine grades A to D, Conflicting, Blocked; mapping is decision D-01 | Repo stores product grades only (`config/grading.yaml`, `Grade` enum). ROADMAP Phase 2 adds verifier agents and evals | **Settled for now:** product grades are the stored contract. Engine grades only after D-01 is answered |
| R-06 | Checklist encoding | `assets/checklist.yaml`: semantic keys, lowercase priorities, `refresh_cadence` words | `config/checklist.yaml`: ids `P1.01`, display priorities, `shelf_life` days; pinned by tests | **Settled: `config/` is canonical.** `scripts/check_alignment.py` keeps the two aligned (it found five cadences config 1.0.0 had missed) |
| R-07 | Run statuses | QUEUED, RUNNING, DISTILLING, VERIFYING, AWAITING_APPROVAL, PARTIAL, BUDGET_EXCEEDED, FAILED, CANCELED, COMPLETED | Repo `RunStatus`: queued, running, paused, failed, completed (data layer step 8 spec). ROADMAP Phase 2 adds pause for approval on overrun | **Settled for v1.** `paused` covers approval waits. Add `partial`, `budget_exceeded`, `canceled` by migration when the credit manager (Phase 2) needs them |
| R-08 | Credit cap location | Money and credits internal; credits never in client views | `intel.runs.credit_cap` (step 8 spec) sits in the client-readable schema | **Open risk.** Client-facing reads select explicit columns, never `credit_cap`. Moving it to `intel_internal` is a small migration |
| R-09 | Stable source code format | `S-0001` per account | Plan said `S001`; golden schema accepts `S\d{3,}` | **Open, low impact.** Store an integer `source_number`; format for display in one function. Recommended display `S-0001` |
| R-10 | Migration lint | `scripts/lint_migrations.py` scans `.sql` files | Repo migrations are Alembic Python files with explicit `op.f()` names | **Settled:** the lint does not apply. Backstops are the repo tests, `alembic check`, `enum_check_drift()` and `check_tenancy.py` |
| R-11 | Secrets delivery | Secrets Manager mounted as files, not environment variables | Phase 0 brief: environment variables only (`app/settings.py`) | **Settled for app code:** read environment variables. Production may populate them from mounted files; DevOps choice |
| R-12 | Credits and money fields | Defect 4: never mix credits and USD | Repo `credit_cap` is integer credits | **Settled:** separate fields (`credit_cap` credits, any `budget_usd` later) |
| R-13 | Raw object keys | `tenant/account/run/<hash>.json.gz` | Plan: `raw/<account_uuid>/<sha256>.json` | **Open, decide at the object storage step.** Recommended `raw/<tenant_id>/<account_id>/<sha256>.json` (ROADMAP s11.4 per-tenant prefixes) |
| R-14 | Build log | `BUILD_LOG.md` at repo root | Repo has `docs/PHASE0_STATUS.md` and `docs/data_layer/PLAN.md` | **Settled:** running log is `docs/BUILD_LOG.md`; phase status files stay |
| R-15 | Source documents on disk | ARCH v2 PDF and detailed roadmap were in the repo root | Removed from the repo in the initial commit; copies in `~/Downloads/`. The production roadmap PDF is `~/Downloads/Account_Intelligence_Production_Roadmap.pdf` | **Settled.** The PRD and service design `.docx` files are in the repo root |

## R-01 options (ask the person; record the answer here and in the decision log)

**A. Tenant-ready now (recommended).** Add `tenant_id NOT NULL DEFAULT <default tenant>` to every `intel` table, `ENABLE` and `FORCE ROW LEVEL SECURITY`, one policy per table `TO app_rw` with `USING` and `WITH CHECK` on a fail-closed `app_tenant()` accessor, a non-owner runtime role with `NOBYPASSRLS`, and a `tenant_txn()` session helper. Use `assets/starter/migrations/0002_rls.sql` in the platform skill as the pattern (with the defect 2 fix). Cost: about one migration plus a session helper now; every query goes through `tenant_txn()`. Then run `check_tenancy.py --strict` in CI.

**B. Follow the roadmap timing.** Leave tables single-tenant (with `accounts.tenant_id` already in place) and add `tenant_id` and RLS in Phase 3 through migrations. Cost: a backfill migration touching every table, and every query rewritten then. `check_tenancy.py --strict` becomes a Phase 3 exit gate.

The legacy `src/abm_generator/` code and `migrations/versions/0001_initial.py` already implement A's pattern for a different schema and can be ported.

## Phase mapping for platform ref 11 (R-03)

| ROADMAP item | Platform work package (detail to reuse) |
| --- | --- |
| Phase 0 environments and CI | WP 1.1 |
| Phase 1 data layer v1 | WP 1.2 (and 1.6 source codes) |
| Phase 1 broker connectors, credit cap v1 | WP 1.4 |
| Phase 1 workflow v1 | WP 1.5 (with R-02: Temporal, not Inngest) |
| Phase 1 grading engine, tracker state machine | WP 1.6 |
| Phase 1 export builders, quality gate v1 | WP 1.8 |
| Phase 1 API v1 | WP 1.3, 1.9 (operator console) |
| Phase 2 credit manager v2, verifier agents, eval harness, observability | WP 1.7 (settlement ideas), 1.10, 1.11 |
| Phase 3 tenancy, auth, client app, exports, metering, connectors | WP 2.1, 2.2, 3.1 to 3.6 (R-01 decides how much is early) |
| Phase 4 refresh, monitors, alerts, CRM write-back | WP 2.3 and platform ref 07 |
