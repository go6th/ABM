# Prompt: build the Tracker state machine (coverage rows with proof, and the completion rule), production grade

Paste everything below the line into Claude Code, opened in the project repo (built with the `account-intelligence-platform` skill).

---

Build the **Tracker state machine**: each of the 33 coverage rows of a run ends **Done, Partial, Not done, Blocked or None, with proof** (queries run, tool category, result count), and **a run cannot complete while a High row is missing a status**. This is backend work. Write production-grade code and prove it with a real harness. Never invent facts about the process, the providers or the account, and never let a model or a caller type a status or a count.

## 0. Ground rules

1. Read `.claude/skills/account-intelligence-platform/SKILL.md` first and obey it. Then read `references/01-product-and-process.md` (tracker rules, quality gate), `references/05-data-model.md` (`coverage_rows`), `references/06-evidence-and-grading.md` (coverage rows, Lead rule, counts name their base), `references/04-run-lifecycle-and-agents.md`, `references/03-tenancy-isolation-security.md`, `references/12-open-decisions-and-verification.md`, `references/13-starter-code-and-known-defects.md`, `assets/checklist.yaml`, `assets/routing.yaml` (`tool_categories`), `assets/client_output_denylist.txt`, `BUILD_LOG.md`, `docs/STATUS.md`. If `docs/sources/` holds the PRD, read section 4 and Appendix A and cite them. Follow the skill's work loop: plan in five lines, tests first, smallest increment, run the gates, log.
2. **Architecture is fixed by the skill:** the database enforces isolation (`tenant_id NOT NULL`, forced RLS with `WITH CHECK`, roles scoped `TO app_rw`, all access through `tenant_txn`); the LangGraph Postgres checkpoint is the durability source of truth; one retry owner per failure class; tenant from the verified token only; ledgers insert-only; no record is ever deleted or overwritten silently. The tracker **never retries a provider call**; retries belong to the node `RetryPolicy`.
3. The five statuses are exactly `done, partial, not_done, blocked, none` (the `statuses` list in `assets/checklist.yaml`). **Do not add a status** (no `pending`, `skipped`, `n/a`). "Unset" is stored as `status IS NULL` and means "no stage has reported yet".
4. No real provider calls, no real LLM calls, no real spend, no real tenant data. Use fakes and recorded fixtures.
5. Never claim a gate passed that you did not run. Never weaken or delete a test to pass. Commit on branch `feat/tracker-state-machine` in small Conventional Commits. Do not push.

## 1. Preflight (before writing code)

Print a table of what exists: `coverage_rows` table and any seeding code, `src/workflow/coverage.py` and `transitions.py`, the connector layer and ledger (`tool_calls`, `args_hash`, call records, typed connector errors), the run state machine and `account_runs`, `finalize`, `tenant_txn`, `scripts/check_rls_catalog.sql`, `tests/isolation`, `config/workflow_v1.yaml` (row ownership), the client-safe rendering code, the starter's run status values (match their exact spelling).
- Missing dependency: define a typed `Protocol` in `src/tracker/ports.py` and an in-memory fake in `tests/fakes/`. Ports needed: `CallLedgerPort` (read-only: resolve a `call_ref` for this tenant and run to a `CallRecord`), `RunInputPort` (read the run's scope, e.g. which lanes are on), `ChecklistSource`, `Clock`. Production adapters that do not exist raise `NotConfigured`; the row then ends Not done with reason `not_available_yet`, never a guess.
- If `src/workflow/coverage.py` exists, **move the logic into `src/tracker/` and leave a thin re-export there** (one implementation, not two). If it does not exist, build in `src/tracker/` and list the Workflow v1 integration as a follow-up.
- If a `coverage_rows` table exists, **extend it expand-only** (add columns, constraints, indexes; never drop or alter an existing column; ask first if that is unavoidable).

## 2. Anti-hallucination protocol (build time)

1. Statuses, priorities, the 33 rows, passes and cadences come only from `assets/checklist.yaml`. Counts you quote (33 rows, 5 Pass 1 and 28 Pass 2, 19 High, 7 Medium, 7 Optional) must be asserted by a test that reads the file, not typed.
2. **The sources give the rule, not the machinery.** The following are **design proposals of mine, not facts from the PRD or architecture**: the row transition table (section 5.2), the outcome derivation (5.3), the completion policy (5.5), the reason-code list (5.4), the per-row attempt cap. Put each in `config/tracker_v1.yaml` marked `status: proposal`, record them in an ADR, and add them to the open-decision register as the next free numbers after D-11 (I propose **D-12 completion policy** and **D-13 tracker transition and derivation rules**). Do not present them as settled anywhere.
3. Derive numbers from the sources: attempts per row = 1 + the planner gap loop cap (2) = 3; Lead proof = three differently worded queries across at least two tool categories; the similarity threshold for "differently worded" is configuration marked `source: TODO, needs product owner`.
4. No vendor or broker name in `src/tracker/`, in reason texts, in rendered output or in any key. Provider identity exists only as `provider_code` inside the internal ledger. The tracker stores **tool category** (`routing.yaml` `tool_categories`) because the client-facing tracker says "tool", never a broker.
5. Before using any PostgreSQL feature (trigger, generated column, column-level grant), run it on the project's test database and read its real behaviour. Do not write SQL from memory and report it as working.

## 3. Anti-hallucination protocol (run time): the TrackerGuard

These are code rules; each has at least one "lying stage" test (section 9.3).

| # | Rule |
| --- | --- |
| T1 | **Status is derived by code from evidence (`derive_outcome`), never accepted from a model, a prompt or a free-text field.** The public write API `record_outcome(row_key, evidence)` has no status parameter. Only the downgrade API (T7) takes a target status. |
| T2 | **Proof comes from the ledger.** A caller supplies `call_ref`s. Query text, tool category, success or failure and result count are read from the ledger record for this tenant and run. A caller-supplied count, query or category that differs raises `ProofMismatch`. A `call_ref` from another run, another tenant or a previous run (refresh) raises `ProofMismatch`. |
| T3 | A call whose result count is unknown can support only Partial, Blocked or Not done, never Done or None. |
| T4 | **None** means one of two checked things: (a) at least one successful query, every successful query returned zero results, nothing failed; or (b) `out_of_scope`: the run input verifiably switches the row's lane off (the PRD records Veeva once as None outside life sciences). `RunInputPort` is consulted; the reason code must be on the configured out-of-scope list. |
| T5 | **Done** needs result count at least 1 and successful calls at least the stage's declared `expected_calls`. |
| T6 | **Blocked** needs at least one failed call with a typed connector error kind and no successful call. **Not done** needs zero calls and a configured reason code. Not done never hides a failure and Blocked never hides "nobody tried". |
| T7 | **Ownership and actors.** Only the row's owning stage (from `config/workflow_v1.yaml`, if it exists) or the planner re-attempt for that stage may upgrade it; `verifier` for `lead_verification` and rows holding Leads; `judge` and `gate` may only **downgrade** (5.2); `finalizer` may only sweep unset rows. Anyone else raises `NotOwner`. |
| T8 | **Proof is cumulative.** The new proof's `call_ref`s must be a superset of the old proof's. A refresh in the same run adds evidence; it never swaps it. |
| T9 | Reason text is a configured **code**; free detail goes to an internal-only field, length-capped, control characters stripped, and passed through the denylist and an other-accounts scrub. Detail is never rendered to a client. |
| T10 | **Client-safe rendering is allow-list based** (explicit field mapping). It never emits provider codes, credit or cost figures, call refs, internal detail, client staff names or other accounts. The rendered output is run through `scripts/scan_client_output.py` in tests. |
| T11 | **The tally is recomputed from rows on every read.** No function accepts a count as input. Every count names its base ("15 of 19 High rows Done"). |
| T12 | **Terminal sweep.** At any terminal state a row still unset is set to Not done with `swept = true` and a `swept_*` reason. A swept row is visible, counted and **blocks COMPLETED**. It never counts as Done or as a normal Not done. |
| T13 | **Lead rule.** `lead_verification` is Done only if every Lead in the run has proof of three differently worded queries across at least two tool categories (the detector from Workflow v1 if it exists, otherwise implement it in `src/tracker/leads.py` and have Workflow reuse it); otherwise Partial. A repeated or near-duplicate query does not count. |
| T14 | When the evidence gate or judge drops claims, affected rows are re-derived from the surviving evidence and downgraded with the downgrade logged (`claims_dropped`). A downgrade never produces None. |

## 4. Architecture to build

```
src/tracker/
  model.py        Status, Priority, Actor, ReasonCode enums; RowEvidence, CallRecord, Proof (frozen pydantic); proof_hash (canonical JSON, no timestamps)
  errors.py       TrackerError > IllegalTransition, NotOwner, ProofMismatch, ProofIncomplete, UnknownRow, VersionConflict, RunNotSeeded, CompletionBlocked, ConfigError
  config.py       loads and validates config/tracker_v1.yaml and checklist.yaml (fail fast at startup; versions and hashes recorded)
  machine.py      the pure row transition table and guard (5.2)
  derive.py       pure derive_outcome(evidence) -> Outcome (5.3)
  completion.py   pure evaluate(rows, policy) -> CompletionVerdict (5.5)
  tally.py        pure tally(rows) and completeness(rows); sort order for rendering
  gap.py          pure open_rows(), gap_pass_result(), ExitReason (section 7)
  leads.py        T13 validator (or a re-export)
  ports.py        Protocols (section 1)
  repo.py         CoverageRepo: Postgres implementation through tenant_txn
  service.py      TrackerService: seed(), record_outcome(), downgrade(), sweep(), verdict(), view(); the only write path
  guard.py        finish_run(): the only place that moves a run to COMPLETED (calls evaluate(), then the optimistic UPDATE)
  render.py       client-safe and operator views (allow-list mapping)
migrations/     one expand-only Alembic revision (section 6)
config/tracker_v1.yaml          transition table, reason codes (with client text), out-of-scope reasons, policy, attempt cap, similarity threshold
docs/tracker/transitions.md     generated from the table by scripts/gen_tracker_docs.py (a test fails if it is stale)
```

Purity rule: `machine.py`, `derive.py`, `completion.py`, `tally.py`, `gap.py` do no I/O, read no clock and no environment. Time comes from the injected `Clock`.

## 5. The state machine

### 5.1 Row states and storage
A row is seeded **unset** from `checklist.yaml` when the run is created (all 33, same transaction as the run, so a run without a tracker cannot exist). Fields: status (nullable), reason code, proof, proof hash, version, attempts, no-progress count, exhausted flag, swept flag, owner stage, last checked at. Seeding pins `checklist_version`, the file hash and the **completion policy** in `run_coverage_meta` so a later config change cannot alter an old run. Re-seeding the same run is a no-op.

### 5.2 Transition table (proposal D-13; a test enumerates all 30 cells)
Actors: `stage` (owner or planner re-attempt), `verifier`, `judge`, `gate`, `finalizer`. "New proof" means at least one new `call_ref` (T8). Same status with an identical proof hash is a no-op that returns `Unchanged`, writes no event and bumps nothing.

| From \ To | done | partial | not_done | blocked | none |
| --- | --- | --- | --- | --- | --- |
| unset | stage, verifier | stage, verifier | stage, finalizer (sweep) | stage | stage, verifier |
| not_done | new proof | new proof | unchanged only | new proof with a failed call | new proof |
| blocked | new proof, successful call | new proof, successful call | no (never launder a block into Not done) | new proof | new proof, successful call |
| partial | new proof | extend proof | judge or gate downgrade only | no | no |
| none | new proof with results | new proof with results | no | no | extend proof |
| done | extend proof | judge or gate downgrade | judge or gate downgrade | no | no |

Downgrades keep the old proof and add a downgrade note and a reason code; they never produce Blocked or None. A failure after Done attaches a note to the proof; it never voids a Done row.

### 5.3 Outcome derivation (`derive_outcome`, proposal D-13)
Inputs: `expected_calls` (int at least 1 for call-based rows), the resolved `CallRecord`s (ok or failed, error kind, result count, category), `cap_hit` flag, `scope` (in or out), `not_attempted_reason`. Rules in order:
1. Scope out and the reason is allowed and verified: **none (out_of_scope)**.
2. No calls: **not_done** with `not_attempted_reason` (missing reason raises `ProofIncomplete`).
3. Successful calls empty, failed calls present: **blocked**; reason is the most severe error kind by a fixed configured order.
4. Successful calls present, results = 0, no failures, successful calls at least `expected_calls`: **none**. Results = 0 with a failure or fewer than expected: **partial**.
5. Results at least 1, no failures, successful calls at least `expected_calls`, no `cap_hit`: **done**. Otherwise **partial** with the reason (`source_failed`, `cap_reached`, `fewer_than_expected`).
Property tests: deterministic; output always in the vocabulary; adding a failed call never moves the result toward Done; adding a successful call with results never moves it away from Done or Partial; the result never depends on call order.

### 5.4 Reason codes (configured, each with client-safe text that passes the denylist)
Start from the sources only: `waiting_on_account_owner` (PRD: relationship rows when no pursuing client), `lane_not_in_scope`, `source_unavailable`, `source_rate_limited`, `budget_cap`, `tool_call_cap`, `page_cap`, `deadline`, `not_available_yet` (port not configured), `claims_dropped`, `hostile_source_dropped`, `swept_failed`, `swept_canceled`, `swept_budget`, `swept_deadline`, `swept_unreported`. Anything else is config you add and list, never code.

### 5.5 Completion verdict (`evaluate`) and the policy (proposal D-12)
- **Hard block (cannot be COMPLETED, cannot be overridden by policy):** the run has no seeded tracker; any row is unset; any row is swept; any row has a status without valid proof. This includes every High row (the rule from the PRD and skill) and, because silent omission is a defect for all rows, the other priorities too.
- **Gaps:** all rows have a status and proof, but a High row is Partial, Not done or Blocked. The pinned policy `high_rows_allowed_for_completed` decides. **Default: `[done, none]`**, which matches the PRD quality metric "every High row Done or None with proof" and ends such runs PARTIAL with the open rows listed. The alternative (`[done, none, partial, not_done, blocked]`, the literal minimum) is a one-line config change; do not choose it for me.
- Verdict fields: `level` (`complete`, `complete_with_gaps`, `cannot_complete`), `can_complete`, typed `blockers` (row key, status, reason code), the tally, and the completeness ratio with its base.
- `guard.finish_run()` calls `evaluate()` and only then runs `UPDATE account_runs SET status='COMPLETED' WHERE id=:id AND status=:expected`. If `can_complete` is false it moves the run to PARTIAL (or the state the caller names for budget, cancel and failure) with the blockers recorded, never COMPLETED.

## 6. Database (one expand-only revision; follow `scripts/lint_migrations.py` rules R1 to R5 including `lint:covers`)
1. `coverage_rows`: PK `(run_id, row_key)`; `tenant_id NOT NULL` FK; FK to `account_runs`; CHECKs for status vocabulary, priority vocabulary, `status IS NULL OR proof IS NOT NULL`, `status IN ('not_done','blocked') => reason_code IS NOT NULL`, `swept => status = 'not_done'`, `version >= 0`. Forced RLS with `USING` and `WITH CHECK`, policies `TO app_rw`. Grants: SELECT, INSERT, UPDATE; **no DELETE**.
2. `coverage_row_events`: insert-only (SELECT and INSERT grants only); columns tenant, run, row key, per-row `seq` (unique with run and key), from and to status, actor, stage, proof hash, reason code, time. Forced RLS. Every state change writes its event in the same transaction as the row update.
3. `run_coverage_meta`: run, tenant, checklist version and hash, seeded row count, pinned policy.
4. **Completion trigger** on `account_runs` (`BEFORE UPDATE OF status`, when the new status is COMPLETED and the old one is not): raises `check_violation` unless the meta row exists, the row count equals the seeded count, and no row is unset or swept. This is the last line of defence behind `guard.finish_run()`; the policy-level rule stays in code. Verify it fires for the `app_rw` role under RLS.
5. Optional (recommended): a SQL function mirroring the transition table with a **drift test** that compares every cell with `machine.py`. If you skip it, say so in the report.
6. Optimistic concurrency: `UPDATE ... WHERE run_id=:r AND row_key=:k AND version=:v`; zero rows raises `VersionConflict`. The repository owns one bounded re-read-and-re-evaluate loop (max 3, rules re-checked each time, never a blind overwrite); the service adds no second retry layer.
7. Extend `tests/isolation` for all new tables and make `check_rls_catalog.sql` return no rows.

## 7. Loops (bounded, recorded, testable)
- **Gap loop** (feeds the Workflow planner gap loop, max 2 passes): `open_rows(run)` returns rows still open (unset, Not done, Blocked, Partial), High before Medium before Optional, then checklist order, **excluding exhausted rows** (attempts reached the cap of 3). `gap_pass_result(before, after)` reports progress if any row moved up (unset, Not done, Blocked below Partial below Done or None) or kept its status with strictly more proof. No progress means the loop ends.
- Exit reasons share one enum with the workflow: `converged` (every High row Done or None), `cap_reached`, `no_progress`, `budget`, `deadline`. Each loop writes its exit reason into the run state.
- **Repository conflict loop:** max 3 attempts, exit `VersionConflict` to the caller.
- **Resume safety:** `record_outcome` is idempotent. Replaying a stage after a kill between the DB commit and the checkpoint produces the same row state, the same proof hash and **no extra event**.
- The LangGraph state carries only a `tracker_digest` (hash of sorted row key, status, proof hash) and loop counters, never rows or proof text. A test caps state size.

## 8. Integration
1. Stages call `service.record_outcome(row_key, evidence)` inside the same `tenant_txn` as their fact writes, so a Done row can never outlive rolled-back facts.
2. `finalize` and the run transitions call `guard.finish_run()`; terminal non-completed paths call `service.sweep(run, reason)` first.
3. The quality gate reads the tally from rows (it is recomputed, never typed) and checks that the rendered tracker matches the rows.
4. `view(run, audience)` returns a client view (tool category, result count, reason text, sorted with the most valuable open work first, tally with bases) or an operator view (adds call refs and internal detail, admin role only). Wire an API route only if a runs router already exists; otherwise expose the function and list the route as a follow-up. Roles stay admin, seller, viewer.
5. Metrics by status and priority only, no tenant label: `coverage_completeness` (High rows Done or None over High rows) recorded at finalize. Logs through the allow-list; proof, queries and detail never logged at info level (a canary test).

## 9. Test harness (build before the service)
Location `tests/tracker/`, real Postgres, injected `Clock`.
1. **Seed tests:** exactly the rows of `checklist.yaml` (keys, passes, priorities); idempotent; versions and policy pinned; editing the checklist mid-run does not change an existing run.
2. **Pure-logic tests:** the full 30-cell transition table against a hand-written oracle in the test; an exhaustive truth table for `derive_outcome` over expected 1 to 3, successful 0 to 3, failed 0 to 2, results zero or positive, cap flag; all `evaluate` combinations for both policies; property tests (hypothesis) for 5.3.
3. **Lying-stage tests (every T-rule):** a FakeStage that claims Done with no calls; cites a `call_ref` it never made; inflates a result count; claims None while the ledger shows results; cites another run, another tenant or a previous run; supplies an unknown tool category; Done with unknown count; Blocked with no failure; Not done with a failure on record; writes a row it does not own; `gate` tries to upgrade; downgrade to None; swapped (non-cumulative) proof; reason detail containing a broker name or another account's name; a row presented as Done after being swept. Each must raise its typed error and leave rows and events unchanged.
4. **Stateful model test (hypothesis `RuleBasedStateMachine`):** random sequences of seed, record, downgrade, sweep, evaluate, finish against a reference model. Invariants: versions monotonic; event `seq` gapless per row; tally equals recomputation; no illegal transition accepted; no COMPLETED with an unset or swept row.
5. **Database defences:** a raw `UPDATE account_runs SET status='COMPLETED'` as `app_rw` is refused while a High row is unset, a row is swept, the meta is missing or the row count is short; allowed when valid. `DELETE` on rows or events and `UPDATE` on events are refused. Direct status writes without proof violate the CHECKs.
6. **Concurrency:** 20 workers on distinct rows (no loss); 10 racing on one row (exactly one wins per version, no lost event, no duplicate event); two tenants on the same domain and run keys (nothing crosses); SIGKILL a worker mid-transaction (atomic: row and event both present or both absent).
7. **Resume matrix:** kill points before the call, between the response and the ledger write, and after the DB commit but before the checkpoint; after resume assert the same final `tracker_digest`, same event count and ledger sum equal to `spent_usd`.
8. **Rendering:** snapshot of the client view for a synthetic account (fixtures named and labelled `synthetic`); order is open work first by priority then checklist order; tally shows bases; `scan_client_output.py` finds nothing; no provider code, credit figure, call ref or detail appears; every reason code has client text that passes the denylist.
9. **Gap loop:** terminates within caps for any generated outcome sequence; no-progress detection; exhausted rows are not retried; exit reasons correct.
10. **Whole-run wiring** (if Workflow v1 exists): the outage account ends PARTIAL with Blocked rows and reasons; the sparse account ends with None rows and no invented facts; a seeded defect (a High row unset) blocks COMPLETED end to end.
Coverage: at least 90% on `src/tracker/`, **100% on `machine.py`, `derive.py`, `completion.py`, `guard.py`, `model.py` proof validation**.

## 10. Development loop (bounded)
For each component repeat at most **5 iterations**: write or extend failing tests; smallest implementation; run its tests, `ruff`, `mypy --strict src/tracker` and the isolation suite; if red, change the **code**. After 5 red iterations on one component, STOP and report the failing test, its output, what you tried and your hypothesis. Do not weaken the test, mock the unit under test, skip it, or mark it `xfail` (except for a defect already recorded in `references/13` or an ADR).
Build order: `model`, `errors`, `config`, `machine`, `derive`, `completion`, `tally`, `gap`, `leads`, `ports` and fakes, `render`, migration, `repo`, `service`, `guard`, integration, docs generation.

## 11. Production requirements
Type hints everywhere and `mypy --strict`; no bare `except`; no global mutable state; no clock or randomness in pure modules; config validated at startup; structured logs with run id as the trace id; one span per `record_outcome` and `finish_run`; idempotent migration that runs twice cleanly; every public function documented with its guarantees; every error typed.

## 12. Gates before you finish
Run and report real results: skill self-test; `validate_assets.py`; `lint_migrations.py`; `check_rls_catalog.sql` (no rows); `ruff check`; `mypy --strict src/tracker`; isolation suite; `pytest tests/tracker` with coverage; the full `pytest -m "not eval"`. Confirm: 33 seeded rows match `checklist.yaml`; the transition doc is current; no vendor name in `src/tracker/`; every T-rule has a passing lying-stage test; the DB trigger refuses an invalid COMPLETED as `app_rw`.

## 13. Deliverables
`src/tracker/`, the migration, `config/tracker_v1.yaml`, `docs/tracker/transitions.md`, ADRs (transition and derivation rules, completion policy, move of coverage logic from the workflow), updated open-decision register (D-12, D-13), tests and fixtures, updated `BUILD_LOG.md` and `docs/STATUS.md`.

## 14. Stop rules
Stop and ask me if: a change would drop or alter an existing column; the starter run statuses differ from the ones assumed here in a way that changes the completion trigger; an existing `coverage_rows` shape conflicts with this design; you want to add a status or alter the architecture in section 0.2; real credentials, spend or tenant data would be needed; a gate stays red after 5 iterations; or the skill and another source conflict outside the register.

## 15. Final reply (short)
A table of the components (Done, Partial, Blocked); gates run with results; what is **proven by tests** versus assumed (state the guarantees precisely: no status without ledger-verified proof, no COMPLETED with an unset or swept row, atomic and idempotent under kill and race); the policy default you applied for D-12 and the proposals in D-13 that need my decision; integration follow-ups (Workflow v1, API route); open `[VERIFY]` items; commits; the next work package.
