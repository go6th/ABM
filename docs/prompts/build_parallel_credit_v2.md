# Prompt: build Parallel execution and Credit manager v2 (fan-out per lane and source type with per-broker concurrency limits; per-run cap with soft and hard limits, live spend, pause for approval, alternate path or pre-pulled upload), production grade

Paste everything below the line into Claude Code, opened in the project repo (built with the `account-intelligence-platform` skill).

---

Build two coupled backend work packages. **Parallel execution** replaces the sequential stage order of Workflow v1 with a dependency-aware scheduler that fans out per **lane** and per **source type**, respects dependencies (person-name searches wait for the people list), and enforces **concurrency limits per broker across all runs**, so parallel runs do not trip rate limits; the target is **under 4 hours of wall clock per account, to be validated, not assumed**. **Credit manager v2** extends Credit cap v1 with a per-run cap with breakdown, **live spend, soft and hard limits**, a **pause for approval** (notification, one click) instead of failing, and, when a broker is out of credits, an **alternate path or a pre-pulled file through an upload step**. Write production-grade code and prove it with a real harness. Never invent rates, limits, durations, prices or process rules, and never let a model schedule, approve, spend or upload anything.

## 0. Ground rules

1. Read `.claude/skills/account-intelligence-platform/SKILL.md` first and obey it (rules 2, 7, 8, 10 and 11 bind here). Then read `references/04-run-lifecycle-and-agents.md` (graph, budgets, one owner per failure class, slices), `references/02-architecture.md` (scaling, concurrency caps), `references/08-api-auth-billing.md`, `references/10-testing-evals-cicd-ops-cost.md` (chaos, SLOs), `references/11-build-plan.md` (spikes 2, 4, 5), `references/12-open-decisions-and-verification.md`, `references/13-starter-code-and-known-defects.md` (defects 1, 3, 4, 8), `assets/routing.yaml`, `assets/checklist.yaml`, `BUILD_LOG.md`, `docs/STATUS.md` and the specs left by earlier work packages in `docs/prompts/`, `docs/workflow/`, `docs/providers/`, `docs/spend/`, `docs/api/`. If `docs/sources/` holds the PRD and SaaS document, cite PRD 4.2 (steps 1 to 13), 4.3, 4.6 and SaaS 4.2 step 4 (steps run in parallel where they do not depend on each other; person-name searches wait for the people step), SaaS section 6.
2. **What the sources say (the whole rule set; do not add to it as fact):**
   - Fan-out is per source group (baseline and executive voice, news, jobs, tech, people, person-name searches, review sites and communities); lanes are Salesforce (including field service), ServiceNow, SAP, Tableau and analytics, Veeva (primary for life sciences, otherwise recorded once as None), Data and AI (PRD 3.3).
   - Dependencies: person-name searches need the people list; full profile pulls need the title-search results; posts only for priority A; the tech cross-check needs the postings; verification of Leads comes after the pulls; distill, build, gate, deliver follow in order.
   - ARCH: real limits are **broker rate limits, LLM rate limits and Postgres connections, not CPU**, so use **per-tenant and per-broker concurrency caps**; wall clock hard ceiling **4 h** (60 slices of 240 s); run p50 under 10 min and p95 under 45 min are **targets to prove** (conflict C7); the in-graph budget guard runs before the call; one retry owner per failure class (the node `RetryPolicy` owns broker 429 and 5xx; no second retry layer).
   - One cap per run, agreed up front, **ask before spending beyond it**, report all spend at the end; **a broker out of credits marks rows Blocked and never fails the run**; pre-pulled files are an operator-supplied alternative when a broker is out of credits (PRD 3.3, 4.6).
   - Roadmap wording for this item: a run that would exceed the cap **pauses for approval (notification, one click) instead of failing**; a broker out of credits **switches to an alternate path or accepts a pre-pulled file through an upload step**.
3. **Fixed by the skill:** the database enforces isolation; tenant identity only from the verified token; the LangGraph checkpoint is the durability truth; pull once; money is an insert-only ledger; no client output names a broker or a credit count; agents have no SQL, shell or URL tool; never send outreach; never delete or overwrite (cap changes and uploads are new rows).
4. No real provider, LLM or cloud calls, no real spend, no real credentials or tenant data. Stub providers that enforce their own rate limits, `synthetic` fixtures and hermetic containers only.
5. Never claim a gate passed that you did not run. Never weaken or delete a test to pass. Commit on branch `feat/parallel-credit-v2` in small Conventional Commits. Do not push.

## 1. Preflight (before writing code)

1. Print a table of what exists: `src/workflow/` (`engine.py`, `config/workflow_v1.yaml`, stages, `runner.py`, the Inngest or `RunScheduler` loop), `src/connectors/` (`call_operation`, `ratelimit.py`, `breaker.py`, `ledger.py`, `budget.py`, `router.py`, `config/providers.yaml`), `src/spend/` (Credit cap v1: `gate`, `priority`, `approval`, `extension`, `prepulled`, `report`), the tracker service, the people and job pipelines' stage entry points, `LlmPort`, the payload store, `tenant_txn`, the notification code if any, the API routers (`src/api/`), the isolation suite.
2. **Prerequisites.** This package **extends** the workflow engine and Credit cap v1. If `src/workflow/engine.py` or `src/spend/` is missing, **STOP and tell me which prompt to run first** (`build_workflow_v1.md`, `build_credit_cap.md`); do not rebuild them here. Missing providers, LLM and notifier are ports with in-memory fakes in `tests/fakes/` (`Notifier`, `ScanPort`, `UploadStore`, `WalletPort`, `Clock`, `LlmPort`); production adapters that do not exist raise `NotConfigured`, never a fake success.
3. **One implementation per rule.** Spend is incremented only by the connector's atomic reserve; stage status only by the tracker; facts only by `record_graded_fact()`; retries only by the node `RetryPolicy`. This package schedules, limits, pauses, routes and records; it never reimplements those.
4. Extend tables **expand-only**; never drop or alter an existing column (ask first if unavoidable).

## 2. Anti-hallucination protocol (build time)

1. **No invented numbers.** The sources give **no** per-provider rate limit or concurrency, no per-unit duration, no soft-limit percentage, no pause timeout other than the workflow's 24 hour approval wait, no upload size or freshness limit, no tenant spend ceiling. Every such value is configuration with `source: TODO, needs product owner` or `status: proposal`, and the system starts at the **most conservative setting** (concurrency 1 per provider until measured). Durations used by the scheduler come from **measured unit events**, never from guesses; with none measured the scheduler says so and uses a flagged conservative value.
2. **Proposals of mine, not facts from the sources** (ADR, register entries at the next free numbers; earlier prompts used D-12 to D-41, so start at **D-42**; new conflicts at the next free `C` number):
   - **D-42 unit of work and merge:** a work unit is `(stage, lane, source type, shard)` with a deterministic key; units run in parallel, but **results are merged in key order by one merge step**, and **source codes (`S-0001`) are allocated in that merge**, not at fetch time, so outputs do not depend on completion order. Unit outputs are persisted to domain tables, not only graph state.
   - **D-43 concurrency governor:** Postgres leases with TTL and heartbeat (no Redis, per the GCP decision), per provider across all runs and workers; fairness across tenants; adaptive limit (AIMD) between a floor of 1 and a configured ceiling.
   - **D-44 the 4 hour target:** measured by a simulation with stub providers and by the staging injector; **real validation needs real runs and credits (spike 4) and is reported Blocked**. Records the C7 conflict (PRD one working day, ARCH p50 10 minutes, ceiling 4 h).
   - **D-45 pause instead of fail (supersedes the D-28 default for unattended runs):** every run that would exceed its cap pauses; one open request per cap version; timeout or decline ends `BUDGET_EXCEEDED` with partial results; a bounded number of pauses; an optional tenant ceiling; one click means one click **after authentication with step-up**, never a bare emailed link that spends money.
   - **D-46 soft limit:** a configurable fraction of the cap; reaching it notifies and switches funding to priority-only (High rows and units already started).
   - **D-47 exhaustion ladder (supersedes the D-31 default, since the roadmap is newer than the PRD's practice; the conflict stays recorded):** pre-pulled file first, then an alternate route allowed by tenant policy (`allow_alternate_on_exhaustion`, default per the roadmap: true) with the result labelled by its real source tier, then Blocked `credits_exhausted` plus an upload request. Uploads are operator-only, scanned, schema-validated, hashed and attested.
3. **Verify every library fact** (LangGraph `Send`, superstep, cancellation and **whether completed sibling tasks' writes survive a cancel**, `interrupt` and resume, Cloud Tasks queue concurrency settings, signed upload URLs, the malware scan option) against installed packages and docs; mark anything unverified `[VERIFY]` in `docs/open-items.md`. Do not state performance numbers; measure them.
4. No vendor or broker name in `src/parallel/`, `src/spend/` additions, tenant-facing output, notifications, config keys or logs. Provider identity is a `provider_code`.

## 3. Anti-hallucination protocol (run time)

### 3.1 ParallelGuard (each rule has a "lying scenario" test, section 9)
| # | Rule |
| --- | --- |
| P1 | **Order-independent outcomes.** Merge by unit key; source codes allocated in the merge. The same inputs and the same provider responses give byte-identical facts, codes and exports under any completion order (randomised interleavings in tests). |
| P2 | **Dependencies are data.** Declared in config, validated at startup (acyclic, every dependency exists, every unit type known). A unit never starts before its dependencies are finished. A unit whose dependency failed is **Blocked `dependency_failed`** with proof, never skipped silently, unless config marks the dependency `soft`. |
| P3 | **Units come only from code and stored rows.** Lanes and source types from config and run input; dynamic fan-outs (top ten people for name searches, per-person profile pulls, gap searches) are computed from stored rows with hard caps. A model may suggest a gap; it can neither create nor alter a unit. |
| P4 | **Concurrency is a database invariant.** In-flight calls per run, per tenant and **per provider across all runs and workers** never exceed their limits (leases with TTL and heartbeat, released in `finally`, reclaimed after a crash). A test monitor asserts `active_leases(provider) <= limit(provider)` continuously. |
| P5 | **Fair and starvation-free.** Per-tenant and per-run share caps on a provider's slots, priority first (High rows), then critical path, then age (aging bounds the wait), then key. A flood from one tenant cannot starve another beyond the configured bound. |
| P6 | **Adaptive and conservative.** On a rate-limit signal the provider's limit halves (floor 1); after a sustained clean window it grows by one (never above the ceiling). The connector keeps rate and cooldown; **no second retry loop** is added here. |
| P7 | **Funding before launch, atomically.** A paid unit is launched only after the connector's atomic reserve succeeds; parallel launches can never jointly exceed the cap (no check-then-act). |
| P8 | **Pull once under parallelism.** Units that need the same feed share one pull (single flight keyed by the ledger key); the second waits or replays; proven with concurrent units. |
| P9 | **Cancellation and slice safety.** A slice deadline or cancel propagates to every in-flight unit through a cooperative token; a paid call either completes and is recorded or leaves a reconcilable intent; completed siblings' outputs are already in domain tables; resume **re-derives the ready set from unit events**, never from memory. |
| P10 | **Deadline aware.** The run has `deadline_at` (default the sourced 4 h ceiling) and a reserve for distill, build and gate; a unit that cannot finish by its measured p95 is not launched and its rows are Blocked `deadline`; the run still distils and delivers what exists. |
| P11 | **Failure isolation.** A failed or poisoned unit never cancels siblings; outcomes are typed; rows are Blocked with a reason. |
| P12 | **Bounded resources.** Bounded queues and in-flight units per worker; **no database transaction is held across a provider or model call**; connections stay within the pool; the worker's concurrency is capped by config. |
| P13 | **The schedule is recorded.** Every unit has queued, started and finished times, provider, lease wait and outcome in an insert-only event table, so the critical path and idle time can be computed after the fact. |

### 3.2 CreditGuard (adds to the K1 to K16 of Credit cap v1; every K-rule still holds and is re-tested)
| # | Rule |
| --- | --- |
| M1 | **Limits.** `soft < hard`, and **hard is the approved cap** (never exceeded). Crossing soft emits exactly one event per cap version (idempotent), a notification, and switches funding to priority-only (D-46); a new cap version re-arms soft. |
| M2 | **Live spend is one consistent read.** `committed`, `held` (in-flight reservations), `cap`, `soft`, `remaining` and `as_of` come from the atomic counters in one query; only `committed` is claimed monotonic. The **projection** (committed plus held plus the estimate of funded pending units) is labelled an estimate and carries its base. |
| M3 | **Pause, not fail.** A cap denial of a funded unit creates **exactly one open request per cap version**, stops new paid launches, lets in-flight calls finish, checkpoints, and moves the run to `AWAITING_APPROVAL` with reason `cap`. Duplicate delivery and races create nothing new. |
| M4 | **The request is computed in code.** Shortfall from the upper-bound estimates of the blocked units, a suggested new cap (rounded up by the configured step, capped by any tenant ceiling), the rows at risk, spend so far with its breakdown. Every number has its base. |
| M5 | **Approval is bound and single use.** An authenticated approver with the configured role and a step-up claim approves the suggested amount or a custom amount within the ceiling, or declines; it binds request id, run, tenant and amount, and expires. Decline, timeout and a late approval end as specified (D-45); the pause count is bounded; a wrong-tenant or replayed approval fails. A new cap version is **appended**, never edited. |
| M6 | **Cap is not provider exhaustion.** "Our cap is reached" and "the provider says its balance is empty" are different classes with different paths; exhaustion never consumes a cap approval. |
| M7 | **The exhaustion ladder is deterministic.** (1) A valid registered pre-pulled file covering the pending operations is consumed; (2) otherwise an allowed alternate route that is not itself exhausted or open-circuit is used, the result labelled with its real route and graded from its real source tier, and a tracker row cannot become Done through an alternate unless its done-criteria are met by evidence; (3) otherwise the affected rows are Blocked `credits_exhausted` and an upload request with the **exact file spec** is raised. Only the units of the exhausted provider are held (bounded wait), other units continue. |
| M8 | **An upload is hostile input.** Operator only; direct to a quarantine bucket by a signed upload; type and size caps; `ScanPort` (an unavailable scanner **fails closed**, the file stays quarantined); parsed in strict mode by the **same normaliser as the live provider**; schema drift rejected; account match checked where the payload allows and otherwise an explicit attestation is recorded; freshness checked against config; sha256 deduplicated; provenance recorded; never altered, never logged, never in client output; the zip rule from D-25 and the terms in D-10 apply. |
| M9 | **An upload maps to exact pending operations.** The spec is generated by code from the pending units; the file must match one; matched operations are satisfied through a ledger entry with route `prepulled` and cost 0, so the pull-once replay serves them; unmatched operations stay pending. |
| M10 | **Notifications are an outbox.** Written in the same transaction as the event, deduplicated by key, at-least-once with an idempotent consumer, bounded retries, content from templates and allow-lists (tenant versions carry no provider names or amounts before D-04), links carry no bearer power. A delivery failure never blocks or fails a run. |
| M11 | **Wallet and cap stay separate** (defect 4). An extension raises the broker cost cap; if it needs more tenant credits it goes through `WalletPort` as a conditional reserve; a refusal ends in the decline path. Settlement stays exactly once per terminal state. |
| M12 | **Nothing silent.** Every pause, soft crossing, exhaustion, alternate use, upload accept or reject, approval and timeout is a run event and an audit row, visible in the operator view. |

## 4. Architecture to build

```
src/parallel/
  config.py  errors.py  models.py        WorkUnit, UnitEvent, Schedule, ReadySet, ProviderLimit
  dag.py                                 load and validate the dependency graph (pure)
  units.py                               build units from config, run input and stored rows (pure, capped)
  scheduler.py                           ready set, ordering, funding, deadline (pure core + async shell)
  governor.py                            leases, fair share, AIMD (Postgres)
  singleflight.py                        shared pulls keyed by the ledger key
  merge.py                               ordered merge, source code allocation, determinism
  cancel.py                              cooperative cancellation token
  executor.py                            bounded async execution of units, no txn across calls
  critical_path.py                       analysis from unit events (pure)
  simulate.py                            virtual-clock simulation harness
src/spend/ (additions)
  limits.py  live.py  pause.py  exhaustion.py  upload.py  notify.py
  (soft and hard limits, live snapshot and projection, pause requests, the ladder,
   the upload step and its validation, the notification outbox)
src/api/routes/spend_v2.py               live spend, extension decline, upload intents and completion
config/parallel.yaml   dependencies, lanes, concurrency limits (proposal), fairness, deadline reserve, aging
config/spend.yaml      soft percent, pause timeout, max pauses, ceiling, upload limits, alternate policy (additions)
migrations/  tests/parallel/  tests/spend/  tests/system/  tests/fakes/  docs/parallel/  docs/spend/  docs/adr/
```

### 4.1 Dependency graph (in `config/parallel.yaml`, tested against the PRD statements)
Independent roots: set-up, pass 1 relationship, baseline, news pull, jobs live board search **per lane**, the 12 month postings pull (once), technology detections pull (once), title search **per lane and seniority pass**, review sites and communities. Dependent: association search after the first merged people pass; **profile pulls per senior person** after the people merge; **posts for priority A** after levels; **person-name searches per top-ten person and source category** after the people merge; platform-sentence reading after the postings pull; **tech cross-check after postings and detections**; Lead verification after all pulls; distill, judge pass, build, gate, deliver sequential. A test proves each of these edges and proves no edge exists that the PRD does not support.

### 4.2 Scheduler
A pure function `next_batch(state, limits, funding, clock)` returns the units to launch: ready (all dependencies finished) and not paused, ordered by priority, critical-path length, age, key; filtered by per-run, per-tenant and per-provider capacity (leases), by funding (the connector's reserve decides), by deadline (P10) and by hold (a provider held for an upload). The async shell executes the batch with bounded concurrency, records unit events, and loops until: `converged`, `deadline`, `budget`, `paused`, `cancelled`, or `no_progress` (nothing ready, nothing in flight, work pending: raise `SchedulerDeadlock`, Block the remainder `dependency_failed`, never hang).

### 4.3 Live spend and limits
`live.snapshot(run)` reads the counters and returns the allow-listed tenant view or the operator view (by provider code and category); `limits.evaluate` runs after every reserve and true-up inside the same transaction as the counter update, emits soft and pause events once, and returns the funding mode (`normal`, `priority_only`, `paused`).

### 4.4 Pause and approval
The workflow's graph interrupt pauses the run (one owner of the wait: the workflow, not a poll loop here); the pause request, its notification and the approval, decline and timeout paths are in `pause.py`; resume re-derives the ready set from unit events and continues with the new cap version.

### 4.5 Upload step (API, operator role)
`POST /operator/v1/runs/{id}/prepulled/intents` returns the exact file spec and a signed upload target; `POST .../prepulled/{intent}/complete` triggers scan, validation and registration and returns a typed result (accepted with the operations it satisfies, or rejected with a reason code); `GET` returns status. Everything is in the API policy table, audited, idempotent, and never returns file content.

## 5. Database (one expand-only revision; follow `scripts/lint_migrations.py` rules R1 to R5 including `lint:covers`)
- `work_units` (insert-only definitions: key, run, stage, lane, source type, shard, args hash, depends_on, provider route, est cost) and `work_unit_events` (insert-only: queued, started, finished, failed, blocked, cancelled, with timestamps, lease wait, outcome, reason).
- `provider_limits` (current ceiling and adaptive limit per provider, mutable and documented as not a ledger) and `provider_leases` (run, tenant, provider, holder, expires_at, heartbeat; mutable, documented); a conditional insert enforces the limit; `provider_limit_events` (insert-only: decreases and increases with reason).
- `spend_limit_events` (insert-only: soft, pause_opened, approved, declined, timed_out), reusing `run_cap_versions` and `run_cap_events` from v1.
- `notification_outbox` and `notification_deliveries` (insert-only, dedupe key unique).
- `prepulled_intents` and `prepulled_intent_events` (insert-only), extending `prepulled_files` from v1; objects in a quarantine bucket separate from the accepted bucket.
- Forced RLS on every table, policies `TO app_rw`, no UPDATE or DELETE on insert-only tables, tenant views unable to read provider columns or operator JSON.

## 6. Loops (all bounded, recorded, testable)
- **Scheduler loop:** bounded by units and the deadline; exit reasons as in 4.2; every iteration advances at least one unit or exits.
- **Lease wait:** a bounded wait with jitter, then the unit is re-queued with its age preserved (queueing, not provider retry); the wait is recorded.
- **AIMD adjustment:** at most one change per window; floor and ceiling enforced.
- **Heartbeat:** a lease renews while its call runs and expires at its TTL if the holder dies.
- **Pause wait:** owned by the workflow interrupt (24 hour timeout, then decline and settle).
- **Notification relay:** bounded retries then a recorded failure; **upload validation:** one pass, no retry of a rejected file.
- No retry over provider work is added anywhere; the node `RetryPolicy` stays the owner. Every loop records its exit reason and is listed in `docs/architecture/loops.md`.

## 7. Integration
1. `engine.py` builds the unit graph from `config/parallel.yaml`; the stage code keeps its logic and becomes the unit body; stages write through their repositories and tracker, and the merge step is the only writer of source codes in a run.
2. `call_operation` takes a governor lease around the provider call and calls the credit gate and the exhaustion ladder; the connector still owns rate, cooldown and the ledger.
3. The pause maps to the existing `AWAITING_APPROVAL` state through the state machine function; cap approval and decline routes register in the API policy table from API v1.
4. Metrics by provider, outcome and class only (no tenant label); OTel spans per unit with the run id as trace id; queue wait and lease wait as span attributes.
5. **Comments:** every module opens with a docstring (purpose, guarantees, failure modes) and each guarantee is tagged `GUARANTEE(<test name>)`, per the master audit prompt.
6. On GCP the queue's own concurrency setting is a coarse backstop only; the governor stays authoritative `[VERIFY]`.

## 8. Anti-hallucination for any model use
No model is used in `src/parallel/` or the spend additions. The planner's gap suggestions enter only through P3. A test greps both packages for LLM client imports and fails if any appear.

## 9. Test harness (build before the code)
Location `tests/parallel/`, `tests/spend/`, `tests/system/`, real Postgres, virtual `Clock`, fakes from `tests/fakes/`, fixtures labelled `synthetic`.
1. **Strict stub providers:** each enforces its own rate and concurrency limit, counts violations, answers 429 with and without `Retry-After`, returns `credits_exhausted`, slow and failing responses, and scripted costs; the harness fails if a stub records a violation attributable to our scheduling.
2. **Hand-written oracles:** three synthetic accounts (rich, sparse, provider outage) with expected units, dependencies, merge order, codes and spend.
3. **Property tests (hypothesis) on pure code:** the DAG validator; `next_batch` never exceeds any limit, never launches a unit before its dependencies, is deterministic, terminates, and always makes progress or exits; `merge` is independent of arrival order; the critical path of a generated DAG equals the longest path; soft and hard arithmetic uses integers and never exceeds the cap.
4. **Virtual-clock simulation** (`simulate.py`): with scripted latencies the makespan equals the critical-path bound when capacity is unconstrained and degrades predictably as limits tighten; produces makespan, critical path, utilisation and queue waits per provider; **the report states it is a simulation and does not claim the 4 hour target is met**.
5. **Concurrency and chaos:** 100 workers and 50 simultaneous runs against the stubs: zero limit violations, no starvation beyond the bound, leases reclaimed after killed workers, single flight for shared pulls, cap never exceeded under parallel launches; two workers on one run; kill between provider response and ledger insert; kill during a pause, during approval, and during an upload; resume re-derives the ready set; deadlocked graphs raise `SchedulerDeadlock`.
6. **Order independence:** run each oracle account under 50 randomised completion orders; facts, source codes and exported rows are identical.
7. **Lying scenarios, every P and M rule:** a model-suggested unit outside the allow-list; a unit with a missing or cyclic dependency; a dependency that fails; a lease holder that never releases; a provider that returns 429 forever; a stale approval, an approval for another run or tenant, a replayed or late approval, an over-ceiling amount, a missing step-up claim; a cap denial and a provider exhaustion in the same run; a pre-pulled file with the wrong account, schema drift, an oversized or wrong type, a failed or unavailable scan, a duplicate hash, stale data, and one that matches no pending operation; an alternate route that is itself exhausted; a notification channel that fails. Each ends in its typed error or documented handling, never a silent pass.
8. **Soft and hard limits:** soft crossing fires once per version; funding switches to priority-only; a new version re-arms; live spend reads are consistent under concurrent reserves; the projection is labelled an estimate.
9. **Visibility:** tenant views and notifications pass `scripts/scan_client_output.py`; no provider name, code, credit count or call reference; operator views are role-gated.
10. **Isolation and database tests:** two tenants never see each other's units, leases, requests, uploads or notifications; insert-only tables refuse UPDATE and DELETE; the migration runs twice cleanly and passes the lint.
11. **Whole-run system tests:** a synthetic account through all stages in parallel to COMPLETED, to PARTIAL with Blocked rows after a provider outage, and through a cap pause, approval and resume; plus a pause that ends in decline with partial results and one settlement.
12. **Scale smoke:** 1,000 units per run and 50 runs on the stubs within a memory cap; measure and report, do not assert numbers I did not give.
13. **Mutation check:** run `mutmut` (or the project's tool) on `dag`, `scheduler`, `governor`, `merge`, `limits`, `pause`, `exhaustion`; list survivors or say it could not run.
Coverage: at least 90% on the new packages, **100% on `dag`, `scheduler` (pure core), `merge`, `critical_path`, `limits`, `exhaustion` (decision logic) and `pause` (state logic)**.

## 10. Validating the 4 hour target (a protocol, not a claim)
Write `docs/parallel/VALIDATION.md`: the measurements to take on real accounts (per-unit durations by provider, queue and lease waits, critical path, makespan, spend, rate-limit signals), the staging injector scenario (50 simultaneous runs against stub providers), the pass criteria taken from the sources (4 h hard ceiling; p50 and p95 as targets), and the **exact inputs I must supply** (real provider limits from their contracts, 20 real accounts and credits, the measured unit durations). The result table ships empty and marked `Blocked`.

## 11. Development loop (bounded)
For each component repeat at most **5 iterations**: write or extend failing tests; smallest implementation; run its tests, `ruff`, `mypy --strict src/parallel src/spend` and the isolation suite; if red, change the **code**. After 5 red iterations on one component, STOP and report the failing test, its output, what you tried and your hypothesis. Do not weaken the test, mock the unit under test, skip it, or mark it `xfail` (except for a defect already recorded in `references/13` or an ADR).
Build order: `config`, `errors`, `models`, `dag`, `units`, `critical_path`, `merge`, `cancel`, fakes and strict stubs, oracles, migration, `governor`, `singleflight`, `scheduler`, `executor`, `simulate`, `limits`, `live`, `pause`, `exhaustion`, `upload`, `notify`, API routes, engine integration, system tests, docs.

## 12. Production requirements
Type hints and `mypy --strict`; no bare `except`; no global mutable state; no clock or randomness in pure modules; typed errors only; config validated and hashed at startup; no transaction across a provider or model call; every state change a single conditional `UPDATE ... WHERE status = <expected>` or an insert; structured logs with the run id as trace id and no money figures or file content at info level; idempotent migration; every public function documented with its guarantee.

## 13. Gates before you finish
Run and report real results: skill self-test; `validate_assets.py`; `lint_migrations.py`; `check_rls_catalog.sql` (no rows); `ruff check`; `mypy --strict`; isolation suite; `pytest tests/parallel tests/spend tests/system` with coverage; the full `pytest -m "not eval"`; the simulation report; `scan_client_output.py` on tenant views and notification fixtures. Confirm: every config entry has a `source` or `status`; every P and M rule has a passing lying test; no vendor name in the new code; the docs and `loops.md` are current.

## 14. Deliverables
`src/parallel/`, the spend additions, API routes, the migration, `config/parallel.yaml` and `config/spend.yaml` additions, `docs/parallel/` (dependency graph, scheduler, governor, VALIDATION.md, runbooks for a stuck lease, a deadlock and a limit storm), `docs/spend/` additions (limits, pause and approval, the ladder, the upload contract and file spec), ADRs (merge and code allocation, governor, pause instead of fail, ladder and alternate default, upload trust), the updated open-decision register (D-42 to D-47, the new conflicts), tests and fixtures, updated `BUILD_LOG.md` and `docs/STATUS.md`.

## 15. Stop rules
Stop and ask me if: `src/workflow/engine.py` or `src/spend/` is missing; the installed LangGraph cannot keep completed sibling work on cancel and a decision depends on it (explain the options); a change would drop or alter an existing column; a source and the roadmap disagree outside the register; the ladder or the pause would require spending beyond an approved cap; real credentials, spend or tenant data would be needed; a gate stays red after 5 iterations.

## 16. Final reply (short)
A table of components (DAG, units, scheduler, governor, single flight, merge, simulation, limits, live spend, pause and approval, ladder, upload step, notifications: Done, Partial, Blocked, and what each depends on); gates run with results; what is **proven by tests** versus assumed (state precisely: dependencies respected, concurrency never above the limits, order-independent outputs, the cap never exceeded under parallel launches, one pause per cap version, bounded approval paths, the ladder order, hostile uploads rejected, no leak); what is **not proven** (the 4 hour target on real accounts, real provider limits, real notification delivery, the real scanner, real Cloud Tasks behaviour); the decisions I must make (D-42 to D-47); what I must supply (provider limits, measured durations, soft percentage, pause timeout, ceiling, upload limits and freshness, approver roles); follow-ups (console pages for the live spend panel, approval prompt and upload, tenant daily cap); open `[VERIFY]` items; commits; the next work package.
