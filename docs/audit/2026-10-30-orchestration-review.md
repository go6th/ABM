# Orchestration and sequence review, 2026-10-30

Read-only review of how one research runs, from `POST /v1/research` to settlement. Nothing was
executed (no tests, no database, no provider). Every finding below was checked by reading the code
around it; the two guides (`docs/guide/02`, `06`) were used as the claim list, not as evidence.
Line numbers are those of the working tree at review time (the tree was still being edited, so
`src/agents/graph.py` lines may drift by a few).

## 1. Summary table of checks 1 to 9

| # | Check | Result |
|---|---|---|
| 1 | Node order and inputs on every route | ok. Every node's input exists on every route; the sense_check/targeted_pulls loop is bounded (`follow_up_rounds` standard 1, deep 5) and cannot skip distill. Two low items: paid calls on an empty fact set (F13), no read-through of `mode=build` pipelines beyond what the guide says (nothing wrong found). |
| 2 | Every path reaches `finish` once, settlement once | ok for the graph (all routers send `failed`/`partial` outcomes to `finish`; `finish` is guarded by `settled`; real settlement is `lifecycle.finish`, replay-safe). Defect in what the outcome *means*: F1, F2, F3 (silent pipeline failures reported as done). |
| 3 | Slice mechanics | defect (F5 crash redelivery is dropped while the dead worker's lease is live, F7 SlotUnavailable and ledger progress counted as "no progress", F4 slice ceiling). Lease order, `begin_slice` idempotency and a failed next-slice enqueue are ok. |
| 4 | State transitions and classification | defect (F4 FAILED + full refund at the slice ceiling for a run that holds results; F11 `entity_mismatch` is PARTIAL and charged; F2 COMPLETED with a failed pipeline). Transition table itself is consistent: every terminal path goes through `lifecycle.finish`/`end_from_active`. |
| 5 | Auto-resume and continuation | ok for double-start and chain limit; defect F6 (a lost enqueue permanently blocks the auto-resume), F12 (incremental keys are recomputed per run: reuse of the feed call is not guaranteed across a resume). |
| 6 | Pipelines node | defect (F1, F2, F3). Sequence jobs -> tech -> stack -> people is serial and one failure does not abort the others, but the failure handling is wrong. |
| 7 | build -> gate -> deliver -> finish | defect (F8 run-level gate failure does not hide the built files). No window found where files are delivered but credits are unsettled that is not recovered. |
| 8 | Timeouts and ceilings | defect (F9: only `pull` has a per-call ceiling; F10 token pause timeout continues the run). Loop-bound asyncio primitives: ok (none left; pacer fixed; `WarmGate` checks the loop). |
| 9 | Anything else | F6 hand-off gaps (job/run created but never started), F13, F14. |

Severity count: high 1, medium 7, low 6.

## 2. Findings

### F1 (high) Transient provider errors kill a whole pipeline instead of being retried by the node
* Where: `src/agents/graph.py` pipelines node, the `except Exception` blocks around `run_jobs` (about
  line 841), `write_detections`/`write_stack` (about 871) and `run_people` (about 918); contract in
  `src/jobs/adapters.py:47` and `src/people/adapters.py:76`.
* Mechanism: the adapters deliberately re-raise `RetryableProviderError` while
  `error.attempt < retry_max_attempts` ("re-raise below the node budget"), because the node
  `RetryPolicy` (`retry_on=RetryableProviderError`, `connectors/service.py:839`) is the single
  retry owner (W6). The `pipelines` node is in `RETRIED_NODES`, but its own `try/except Exception`
  swallows that exception before LangGraph can see it. `write_detections` also ends with
  `except RetryableProviderError: raise` (pipelines.py, intent: retry), which the node then swallows.
* Failing scenario: the careers/job feed answers 429 or 503 once on one of its hundreds of pages
  (attempt 1 of 3, or of 10 with caps off). `JobsPipeline` does not catch it; `run_jobs` raises; the
  node logs `pipelines.jobs_failed`, sets `pipelines_jobs_failed=1`, and goes on. Result: no job
  postings are classified, the Job Postings workbook is empty or short, the run can still end
  COMPLETED (see F2). Same for people.
* Smallest fix: in each of the four handlers add `except RetryableProviderError: raise` before
  `except Exception` (after the `TokenGuardReached` line). After the node budget is spent the adapters
  convert the error to `SourceUnavailable`, which the pipelines already handle, so the pipeline then
  ends with a stated source failure instead of an exception. Idempotency on re-run: postings are
  upserted, claims are recorded once per content, model replies replay, so a node re-run is safe
  (re-run cost is time only).

### F2 (medium) A failed pipeline is reported to the tracker as Done when Phase 1 left any call evidence
* Where: `src/agents/graph.py` jobs fallback (about line 849-853); `src/agents/production/tracker.py:121`
  (`None if refs else reason_for(...)`).
* Mechanism: the fallback calls `tracker.report(ctx, key, row.call_refs, "source_failed")` for the
  posting rows. `TrackerAdapter.report` merges the row's stored proof with the passed refs and applies
  the reason only when the merged refs are empty. When Phase 1 pulled the feed in time (small account:
  no `owned_by_pipeline` hand-off) the refs are non-empty, so the reason is dropped and the status is
  derived from Phase 1 ledger evidence: Done or Partial. Tech does the same with `[]` but inherits the
  proof stored by `interpret`.
* Failing scenario: `run_jobs` raises a non-retryable error after the feed was pulled (store error,
  invariant error). The posting rows read Done, `deliver` computes `complete=True`, the run ends
  COMPLETED with an empty Job Postings workbook. Only the counter `pipelines_jobs_failed` and an
  `exits` value record the failure; nothing user-visible or in `target()` reads them.
* Fix: on a pipeline failure report the rows with `ReasonCode.SOURCE_FAILED` through a path that does
  not merge prior proof (or `tracker.downgrade(... Actor.GATE ...)` to Partial with `source_failed`),
  and make `AbmGraphFactory.target` treat `pipelines_*_failed` counters as `steps_waiting`.

### F3 (medium) A tech row handed to the pipeline at the 540 s ceiling stays "Not done: source_failed"
* Where: `src/agents/graph.py` pull ceiling (about line 481, `owned_by_pipeline`), interpret loop
  (skips only jobs/people rows), `src/agents/production/tracker.py` `REASONS` (no entry for
  `owned_by_pipeline`, so it maps to `source_failed`), `pipelines.py:396 write_detections` (no
  tracker call).
* Failing scenario: the technology-detections feed is slower than 540 s. `pull` blocks the row as
  `owned_by_pipeline`; `interpret` reports it with that reason (-> Not done, source_failed) because only
  jobs and people rows are skipped there; `write_detections` later pulls and writes the detections but
  never re-reports the row. Tech workbook full, tracker row Not done, run PARTIAL `steps_waiting`.
  Guide 06 section 9 says pipeline-owned rows (jobs, tech) are reported by the pipeline with its own
  evidence; that is true for jobs only.
* Fix: extend the interpret skip to `pipeline in ("jobs","people","tech")` when `deps.pipelines` is
  set and report the tech rows from `write_detections` (calls = its ledger refs), or map
  `owned_by_pipeline` to a non-terminal state that the pipeline then closes.

### F4 (medium) The slice ceiling ends a run FAILED with a full refund even when it holds results
* Where: `src/account_intelligence/workflow/runner.py:194` (`_finish(deps, FAILED, "slice_ceiling", True)`),
  `config/workflow_v1.yaml` `max_slices: 8`.
* Mechanism: every slice that starts counts, including slices spent on approval resumes, token-guard
  resumes and spend pauses (`approve` enqueues slice `slices_used`). A run that needs a ninth slice
  (large account, or several pauses) is FAILED and `refund_all=True`. Every other early stop uses
  `has_usable_output` to choose PARTIAL (charged for real spend, results kept) or FAILED (refund); this
  one does not. `slice_ceiling` is not in `ELIGIBLE`, so nothing resumes it.
* Failing scenario: a research that has pulled, read and graded for 4 h 5 min reaches slice 9: status
  FAILED, credits refunded, platform absorbs the spend, and the user sees a failure for a run with
  facts and files.
* Fix: use the `_end_no_progress` logic (PARTIAL when usable, reason `slice_ceiling`; add it to
  `ELIGIBLE` if a continuation should follow), or do not count approval resumes against the ceiling.

### F5 (medium) After a hard worker death, Cloud Tasks' retries are dropped as `noop` while the dead worker's lease is live
* Where: `src/account_intelligence/workflow/runner.py:272 _yield` and `_begin` (the `slice.resumed`
  branch); lease TTL `config/providers.yaml:36` (1800 s, renewed only by paid-call claims,
  `ledger.py:_upsert_lease`); queue `infra/stack/main.tf:238` (5 attempts, backoff 10 s to 300 s).
* Mechanism: when a worker is OOM-killed or segfaults, its lease row stays valid up to 1800 s. Cloud
  Tasks retries the slice within 10-150 s. The retry cannot take the lease (`LeaseUnavailable`), and
  `_yield` answers `noop` (200) because `slices_used > slice_no`; the task is completed and dropped. The
  `slice.resumed` fix (known problem 7) only runs when the lease is free, i.e. after a graceful
  SIGTERM that released it in `finally`. After a hard kill the run waits for lease expiry + 600 s
  (`runs_orphaned`), then `worker_lost`, then a whole new continuation run: 30-40 min and a second run.
* Fix (smallest): in `_yield` return `busy` (503) instead of `noop` when the lease holder is not this
  process and the run's last begun slice is `slice_no` and the run is still resumable, and give the
  queue enough attempts/backoff to outlast the lease; better, shorten the lease TTL and renew it with a
  background heartbeat so a dead worker's lease lapses inside the retry window.

### F6 (medium) Non-atomic hand-offs can leave a job or run that nothing ever starts
Four places commit a state and then call Cloud Tasks outside the transaction with no recovery sweep:
1. `api/routes/research.py:209` (`run_or_enqueue` after the job commit): enqueue failure -> job
   `queued`, credits reserved, HTTP 500; `open_job` (`credits.py:183`) then blocks any new research on
   the account.
2. `replica/jobs.py:357 run_job`: `claim` commits `running`, then `engine.run` creates the run. A kill
   between them leaves the job `running` with no run; redelivery returns `skipped` (not queued). No
   sweep covers jobs with no run (`_close_ended_jobs` joins on a run).
3. `replica/jobs.py:252`: `create_run` commits (job linked, run QUEUED), then
   `enqueue_slice(..., 0)`. If that raises, `run_job`'s `except Exception` settles the job `failed`
   and releases the credits, while the run row stays QUEUED forever: `runs_orphaned` lists only
   RUNNING/DISTILLING/VERIFYING (`migrations/0050:20`).
4. `replica/auto_resume.py:360`: the continuation job is reserved and committed, then
   `jobs.run_or_enqueue(...)` runs outside the per-run try. A failure aborts the pass, and the
   candidate query excludes any run that already has a continuation
   (`auto_resume.py:263 NOT EXISTS ... resume_from`), so that continuation is never enqueued again and
   blocks the account (`JobOpen`).
Also `runner._end` moves RUNNING -> AWAITING_APPROVAL and then arms the timeout task; if
`schedule_timeout` fails the run waits for a person with no 24 h expiry (the sweeper skips
AWAITING_APPROVAL).
* Fix: one reconciliation step in `sweep_pass`: (a) jobs `queued` older than N minutes -> re-enqueue
  the (named, idempotent) job task; (b) jobs `running` with no run for N minutes -> re-queue; (c) runs
  QUEUED with `slices_used = 0` older than N minutes -> re-enqueue slice 0 (named task, idempotent);
  (d) in `run_job` do not settle the job failed when a run already exists, re-enqueue instead.

### F7 (medium) A run that is only queueing, or only paying for provider calls, is ended `no_progress`
* Where: `src/account_intelligence/workflow/runner.py:380` and `:432`, `runs/lifecycle.py:model_replies`.
* Mechanism: (a) `SlotUnavailable` (provider governor could not grant a slot within its bounded wait)
  ends the slice like a timeout and the next slice is queued at once; three in a row end the run.
  (b) "progress" is the checkpoint id or new MODEL replies. Provider calls completed in a slice (ledger
  `tool_calls`) do not count, although `account_runs.progress_at` already records every ledger event
  and model call.
* Failing scenario: several researches share a provider ceiling; one of them waits 30 s for a slot,
  ends the slice, retries immediately, and is PARTIAL `no_progress` after about two minutes although
  other runs are making the provider busy (this is the mechanism behind known problem 15; the
  `waiter_ttl_s` change only shortens it). Or a `pull` over a slow provider completes a few calls per
  slice but never finishes the node.
* Fix: compare `progress_at` (or a count of done `tool_calls`) before and after the slice instead of
  `model_replies`, and do not count a `SlotUnavailable` end (re-queue it with a delay) toward
  `max_no_progress_slices`.

### F8 (medium) A run-level gate failure does not hide the built files
* Where: `src/account_intelligence/replica/workspace.py:755-775 deliverables`, `src/documents/gate.py`
  (`FILE_CHECKS`, `withheld_files`).
* Mechanism: `gate` ends the run `gate_failed` (PARTIAL, message "the quality check held the files back")
  for any run-level defect (fact without source, tracker row without status, coverage not deliverable,
  gate not configured). `build` has already stored the files in `deliverables`. The listing and the
  download exclude only files named by FILE-level defects, so the files of a run-level failure are still
  listed and downloadable as the account's newest version.
* Fix: in `deliverables()` also exclude every file of a run whose latest `run_gate_results` row has
  `verdict = 'fail'` with a run-level defect (or store the run's final reason and filter on
  `gate_failed`). Open question: the rebuild path (`POST /v1/exports`) does not run the gate and
  reuses the run id, so it inherits the old verdict.

### F9 (medium) Only `pull` has a per-call ceiling; other nodes can hang past the slice and end `no_progress`
* Where: `src/agents/graph.py` `person_searches.search_one` (about line 954), `targeted_pulls.pull_one`
  (about 1116), `entity_lock`, `_careers`, and the pipelines' own calls; compare `pull_one` with
  `asyncio.timeout(PULL_ROW_CEILING_S)`.
* Mechanism: with caps off the provider limits are raised (`connectors/settings.py` `uncapped_config`:
  http total 1200 s, rate-limit wait 1800 s, provider job 7200 s), longer than a 1650 s slice. A single
  call that never answers in `person_searches` holds the node (no checkpoint, no model reply) in three
  consecutive slices: the same failure as known problem 1, fixed only for `pull`.
* Fix: wrap each call in `search_one`, `targeted_pulls.pull_one`, `entity_lock` and the pipeline page
  reads in the same ceiling and treat a timeout as a failed source.

### F10 (medium) A timed-out token-guard pause continues the run (spend guard bypass)
* Where: `src/agents/graph.py` `token_gate` (about line 769), `runs/lifecycle.py:861 expire_cap_pause`,
  `workflow/runner.py:369` (`Command(resume=True)`).
* Mechanism: the token pause is an interrupt with `awaiting: "cap"`, so after `pause_timeout_hours`
  (24, `config/spend.yaml:86`) `/tasks/approval-timeout` takes the cap-pause branch: it records a
  timeout, moves the run to RUNNING and queues the slice, and the runner resumes every interrupt with
  `Command(resume=True)`. In `token_gate` a `True` decision means "continue": `accept_tokens` raises the
  guard by another multiple of the estimate and the paused node re-runs. A person who never answered
  has therefore approved. The `if not decision` branch (`token_guard_stopped`) is unreachable from the
  runner. Decline of a spend pause is also read as approve by `spend_gate` (the ledger cap gate still
  refuses the call, so that one only costs extra pauses, up to `MAX_SPEND_PAUSES`).
* Fix: make the resume value carry the decision (`Command(resume=<decision>)`; a timeout or decline
  resumes with `False`), or make `expire_cap_pause` end a token-paused run `token_guard_stopped`.

### F11 (low) `entity_mismatch` is PARTIAL and charged
`PRE_SPEND_FAILURES` (`wiring.py:55`) lacks `entity_mismatch`, so the run that stops at the wrong
company (after one page read and one search) ends PARTIAL with real spend charged (rounded up to at
least one credit, `credits.py:103`), no stop text (`workspace.STOP_TEXT` has no entry), and holds no
usable output. The graph docstring says it stops "before any money is spent". Fix: treat it like the
other pre-spend failures (FAILED, `refund_all`).

### F12 (low) Incremental inputs are recomputed for a continuation run
`lifecycle._incremental_keys` runs inside `create_run` for every run, including a continuation, using
the newest delivered base and `now.date()`. The continuation can therefore ask the feed for a different
`since` than the stopped run (a base that was delivered or rebuilt in between; a base the stopped run
itself became when it died after building; a month change moves `window_start` and the run's
`as_of`). The feed call key (and `write_detections`' month anchor) then differ, so the "reused at no cost"
guarantee does not hold for that call, and a posting delta may be pulled twice. Correctness is safe (a
base is only used when its pass converged and matching inputs are equal). Also, `provider` Phase 1 uses
`postings_since(run_input)` even when `repo.load_previous` later returns nothing, in which case the
pipeline pulls the full window under another key (double pull of the window). Fix: copy
`postings_previous_run` and `postings_since` from the stopped run's `run_input` when `resume_from` is
set.

### F13 (low) Paid planner calls on an empty fact set
`sense` and `distill_node` have no early return when `deps.grading.facts` is empty; `build` then ends
`no_new_findings`. Two paid planner calls are made for nothing. Fix: route to `build`/`finish` when
there are no facts after `person_searches`.

### F14 (low) Transient errors in build/gate/deliver end the run for good
`build`, `gate`, `deliver`, `finish` are not in `RETRIED_NODES`; any exception there goes through
`_stop_unexpected` (PARTIAL with the class name, `runner.py:404`) and is not in `ELIGIBLE` for
auto-resume. A one-off storage or database error in the last four nodes costs the delivery; the owner
must rebuild by hand. Fix: retry those nodes on infrastructure errors, or add the common infrastructure
error classes to `ELIGIBLE`. Similarly a provider outage that outlasts the node retries ends PARTIAL
with a class-name reason that is not auto-resumed (open question 2).

## 3. Things verified fine
* Node order on every route (full, pull, build; token_gate returns; sense_approval; targeted_pulls
  loop): every node's input is produced earlier; `pull`-mode ends at `finish` with outcome `running` ->
  `completed` -> PARTIAL `pull_only`; build mode reads the parent's manifest and lock (`load_manifest`,
  `_manifest`, `load_entity_lock`) and does not need `cap_approval`.
* The sense_check <-> targeted_pulls loop ends: `second = sense_pass >= rounds` forces record-only and
  `save_sense_check` runs on the last pass; declined approvals clear `awaiting`; `build` requires the
  sense record by digest (G6).
* Routers: `failed` and `partial` outcomes always go to `finish` (`ok_or_finish`, `after_sense`,
  `after_build`, `after_gate`, `after_token_gate`); `finish` is guarded by `settled`; settlement is
  `lifecycle.finish` -> `settle_once` (job row `FOR UPDATE`, replay returns the first result,
  `_close_ended_jobs` sweeps jobs whose run already ended).
* Model failures (`MODEL_FAILURES`) become `failed` outcomes -> PARTIAL with a code, never FAILED with
  a refund; `PRE_SPEND_FAILURES` -> FAILED with a refund; `capped` -> BUDGET_EXCEEDED;
  `target()` and the tracker guard (`finish` demotes COMPLETED to PARTIAL when a row is open).
* Lease order: lease taken before `begin_slice`, released in `finally`, the next slice queued after the
  release (fresh epoch, so a cancelled call reads as abandoned). `begin_slice(expected=)` is an atomic
  `UPDATE ... WHERE slices_used = :expected`. A failed next-slice enqueue returns 500; the redelivery
  resumes the slice (`slice.resumed`) and, if every retry fails, the orphan sweep and auto-resume take
  over.
* Auto-resume: deterministic `request_id` (uuid5 of the stopped run), `lease_live` guard, chain limit
  by walking `resume_from`, one open job per account (`JobOpen`) stops a second continuation, manual
  and automatic resume cannot both start.
* Cloud Tasks task names are per (run, slice number) and slice numbers only grow, so tombstoned names
  are not reused; `AlreadyExists` is success.
* Loop-bound primitives: `RequestPacer` uses a thread lock; `WarmGate` checks `leader.get_loop()`;
  all `asyncio.Lock/Semaphore` in the research path are created per call inside the running loop.
* `tracker.sweep` at a non-COMPLETED end and `close_spend` run in the finishing transaction; terminal
  runs replay settlement without a second report.

## 4. Open questions
1. F1 expects `RetryableProviderError.attempt` to be the ledger attempt of the failing call (set in
   `call_operation`, `connectors/service.py` near line 550). Confirm against a staging log of
   `pipelines.jobs_failed` (the log carries the exception class).
2. Should a provider outage that exhausts the node retries (reason = exception class name) be auto-
   resumed like `model_unavailable`? Guide 02 section 2.7 mentions only the model.
3. A cancelled call (row ceiling, slice timeout, TaskGroup cancel) leaves a `started` ledger attempt
   (`connectors/service.py:12`). A later identical claim by the same worker in the same lease epoch
   returns `InFlight` until `in_flight_wait_s` (1200 s with caps off) or `in_flight_stale_s` (7200 s).
   The documented hand-off "pipeline re-issues the same keys and replays" therefore waits on a call
   that no longer exists when the pipeline runs in the same slice as the cancelled pull; the guide's
   "pipelines.jobs completes about 18 minutes in" may partly be this wait. Confirm from the log time
   between `pull.row_slow` and the first pipeline page call. A cancelled attempt in a later epoch is
   abandoned and charged its reserve again (`ledger.py` stale branch, `possible_duplicate`): a slice
   timeout with eight provider calls in flight bills eight reserves twice.
4. `deliverables()` takes the newest version per kind across runs. After a run-level gate failure or
   a rebuild through `POST /v1/exports` (no gate run, same run id, old verdict reused) which files the
   user sees is not what the gate decided; decide whether a rebuild must re-run the gate.
5. `previous_delivered_run` accepts PARTIAL runs that have `deliverables` rows, which includes a
   `gate_failed` run whose files were built but not released. Its stored job pass is checked
   (`converged`, `pull_complete`), but confirm that a carried posting from a run the gate rejected is
   acceptable as the base.
6. `ExportBuilderPort._built` caches the build per process; a redelivered slice on another process
   rebuilds. Confirm the builder is idempotent per set hash (`exports/store.py` version rows) so a
   second build does not create a second visible version.
7. The sweeper treats a run whose graph already finished (checkpoint at END) but whose `_finish` never
   committed as `worker_lost` and auto-resume then starts a second full research. The window is small
   but a check of the checkpoint (`snap.next == ()`) before ending would close it.
