# Fix wave D1: orchestration, 2026-10-30

Implements the findings of `docs/audit/2026-10-30-orchestration-review.md` (F1 to F14) that belong
to the orchestration files. Nothing is committed (owner rule). No migration was added, so
`EXPECTED_REVISION` is untouched. No paid run, no live provider: every test uses hermetic fakes
or the scratch database `ai_test_d1` / `ai_test_d1b` (own names, `PGPASSWORD=dev`, only
`APP_RW_PASSWORD` taken from `.env`, `.env` never sourced).

Every finding was verified against the code before it was changed. All were real except where a
row below says otherwise.

## 1. Result per finding

"Old" = the same test run against the pre-change `graph.py` / `wiring.py` / `runner.py` /
`lifecycle.py` / `jobs.py` / `auto_resume.py` (overlay copy of the source, the rest of the tree
unchanged). Lists of the failing tests on old code are in section 4.

| # | Status | What changed | Proving tests (fail on old, pass now) |
|---|---|---|---|
| F1 high | fixed | `agents/graph.py` pipelines node: `except RetryableProviderError: raise` before the generic handler in all four places (jobs, tech, stack, people). `agents/production/pipelines.py`: `write_detections` / `write_stack` follow the adapters' rule (re-raise below `retry_max_attempts`, a stated `source_failed` above it) | `tests/agents/test_orchestration_fixes.py::test_a_retryable_pipeline_error_is_retried_by_the_node_not_swallowed[run_jobs, write_detections, write_stack, run_people]`, `::test_the_detections_writer_hands_a_429_up_below_the_budget`, `::test_the_detections_writer_states_a_source_failure_after_the_budget` |
| F2 medium | fixed | `report_source_failed` (graph.py): the rows of a failed pipeline are reported `source_failed`, and a row that still reads Done is taken to Partial through the tracker's own downgrade. `deliver` and `AbmGraphFactory.target` never end a run COMPLETED while `pipelines_*_failed` is set: PARTIAL `steps_waiting` | `::test_a_failed_jobs_pipeline_leaves_failed_rows_and_a_partial_run`, `::test_a_completed_graph_with_a_failed_pipeline_counter_maps_to_partial`, `::test_a_failed_source_downgrades_a_done_row_on_the_real_tracker` (real Postgres tracker). The fake tracker now mirrors the real one (a reason is ignored once the row has calls); `test_a_store_error_in_the_detections_writer_blocks_its_row` changed from `blocked` to `partial` accordingly |
| F3 medium | fixed | `interpret` skips a tech row whose Phase-1 status is `owned_by_pipeline`; the pipelines node reports it with the feed's ledger refs once `write_detections` wrote (`PipelineOutcome.calls`), or as a failed source when it could not | `::test_a_tech_feed_handed_to_the_pipeline_is_reported_by_the_pipeline`, `::test_a_tech_feed_the_pipeline_cannot_pull_either_is_a_visible_failure` |
| F4 medium | fixed | `workflow/runner.py`: the slice ceiling ends PARTIAL `slice_ceiling` when the run holds anything usable (credits by real spend), FAILED + refund only when it holds nothing (`_end_stopped`, shared with `no_progress`). Each wait for a person adds one slice to the ceiling (`lifecycle.note_pause`, `run_input.pause_slices`) | `tests/workflow/test_orchestration_runner.py::test_the_slice_ceiling_*`, `::test_slices_spent_waiting_for_a_person_*`; `tests/runs/test_orchestration_db.py::test_each_wait_for_a_person_adds_one_slice_to_the_ceiling` |
| F5 medium | fixed | The runner renews the lease every 20 s (`_drive`); `connectors/ledger.py _upsert_lease` lets another worker take a lease whose owner has not renewed it for 90 s (`lease_stale_after_s`) before the 1800 s ttl runs out; the retry of the last begun slice answers 503 instead of being dropped (`_yield(other_worker=True)`); a worker that finds its lease taken stops its slice (`LeaseLost`). Why 90 s and the ttl kept at 1800 s: section 3 | `tests/connectors/test_edges.py::test_a_lease_its_owner_stopped_renewing_can_be_taken_*`, `tests/workflow/test_orchestration_runner.py::test_a_redelivery_that_meets_another_workers_live_lease_*`, `::test_the_lease_is_renewed_while_a_node_runs_and_a_lost_lease_stops_the_slice`, `::test_a_slice_that_lost_its_lease_ends_without_ending_the_run`, `tests/unit/test_slice_config_consistency.py::test_a_killed_workers_lease_can_be_taken_by_the_last_retry_of_its_slice` |
| F6 medium | fixed | New `runs/recovery.py`, called from every `sweep_pass` (`api/main.py _recover_handoffs`): queued jobs, jobs claimed by a dead worker with no run, runs that never began slice 0, waits past their deadline (the timeout task never created or never ran) are started or expired again. `run_or_enqueue` and the auto-resume pass log an enqueue failure instead of raising; `GraphEngine.run` no longer settles the job failed when only slice 0's enqueue failed (the run is committed). `schedule_timeout` failing in `_end` is logged | `tests/runs/test_recovery.py` (6 tests), `tests/replica/test_engine.py::test_a_lost_slice_zero_enqueue_*`, `::test_a_queue_that_cannot_be_reached_*` |
| F7 medium | fixed | `lifecycle.completed_calls` (finished `tool_calls`) joins checkpoint and model replies as progress; `SlotUnavailable` ends the slice as queueing: it never counts toward `max_no_progress_slices` and the next slice starts 30 s later (`SliceOutcome.delay_s`, `slot_retry_delay_s`). Three truly empty slices still end the run | `tests/workflow/test_orchestration_runner.py::test_provider_calls_finished_in_a_slice_are_progress`, `::test_a_slice_that_only_waited_for_a_slot_*`, `::test_three_slices_that_truly_do_nothing_still_end_the_run` |
| F8 | not mine | `replica/workspace.py` is D2's. Nothing changed | |
| F9 medium | fixed | Ceilings: `person_searches.search_one` and `targeted_pulls.pull_one` use `PULL_ROW_CEILING_S` (540 s; a slow one is a failed search / `provider_slow`); `entity_lock`'s two calls 300 s each (unreadable, never a hung run); the jobs and people adapters bound single-request operations (search, page read, scrape, org lookups) at `SINGLE_CALL_CEILING_S` 300 s (`source_slow`); `write_stack` 300 s. The feeds and people lists are bounded by the slice only, on purpose (a big account's feed paginates for ~20 minutes and resumes in the next slice; a ceiling would turn that into a gap). Ledger "started" attempt issue: section 5 | `::test_a_person_search_that_never_answers_*`, `::test_a_follow_up_pull_that_never_answers_*`, `::test_a_pipeline_page_read_that_never_answers_is_a_gap` |
| F10 medium | fixed | The graph is resumed with the decision, not always `True`: a wait that lapsed (`expire_cap_pause` marks the number of the slice that resumes it) or a spend pause that was declined or timed out resumes with `False`, so a token pause nobody answered ends `token_guard_stopped`. Extra hole closed on the way: a redelivered slice that finds an unanswered interrupt (first copy died before it recorded the wait) now records the wait instead of resuming it as an approval | `tests/workflow/test_orchestration_runner.py::test_a_resumed_interrupt_is_told_no_*`, `::test_the_graph_is_resumed_with_the_decision_*`, `::test_a_redelivered_slice_that_finds_an_unanswered_interrupt_records_the_wait`; `tests/runs/test_orchestration_db.py` |
| F11 low | fixed | `entity_mismatch` joins `PRE_SPEND_FAILURES`: FAILED with a full refund | `::test_an_entity_mismatch_is_a_failed_run_with_nothing_charged` |
| F12 low | fixed (continuation part) | `_incremental_keys` copies `postings_previous_run` / `postings_since` from the run being continued. Not changed: the review's second half (Phase 1 uses the delta bound although `load_previous` may return nothing) because the base is chosen from the same stored records, so the window is rare to miss; noted | `tests/runs/test_orchestration_db.py::test_a_continuation_keeps_the_incremental_inputs_*` |
| F13 low | fixed | `sense` and `distill` return at once when no fact was graded (the build then ends `no_new_findings`): no paid planner call | `tests/agents/test_failure_paths.py::test_a_run_that_graded_no_fact_finishes_with_no_new_findings` (now asserts zero calls) |
| F14 low | fixed | `build`, `gate`, `deliver`, `finish` retry transient infrastructure errors (dropped connection, timeout, deadlock/serialization, storage 5xx/429) with the node policy's backoff and budget; the provider nodes retry them too. One that outlasts the retries ends the run with its class name, which `auto_resume.INFRASTRUCTURE_STOPS` now continues automatically. The build is safe to repeat (`exports/store.py` stores a file set once per set hash) | `::test_which_errors_are_transient_infrastructure`, `::test_a_dropped_connection_in_build_gate_or_deliver_is_retried_*`, `::test_a_defect_in_deliver_is_not_retried`, `tests/replica/test_auto_resume.py::test_a_dropped_connection_that_ended_a_run_is_continued_*` |

## 2. Files changed

Source (orchestration scope): `src/agents/graph.py`, `src/agents/production/{pipelines,provider,wiring}.py`,
`src/agents/ports.py` (`PipelineOutcome.calls`), `src/account_intelligence/workflow/runner.py`,
`src/account_intelligence/runs/lifecycle.py`, `src/account_intelligence/runs/recovery.py` (new),
`src/account_intelligence/replica/{jobs,auto_resume}.py`, `src/account_intelligence/api/main.py`
(sweep step), `config/providers.yaml`.

Source outside the list you gave (needed by the findings; D2 does not touch them): 
`src/account_intelligence/connectors/{ledger,settings,factory,base}.py` (lease takeover rule,
config fields, the single-request ceiling constants), `src/jobs/adapters.py`,
`src/people/adapters.py` (the per-call ceiling).

Tests: new `tests/agents/test_orchestration_fixes.py`, `tests/workflow/test_orchestration_runner.py`,
`tests/runs/test_orchestration_db.py`, `tests/runs/test_recovery.py`; extended
`tests/agents/{fakes,test_research_core,test_failure_paths}.py`, `tests/workflow/test_slice_drive.py`,
`tests/replica/{test_engine,test_auto_resume}.py`, `tests/connectors/test_edges.py`,
`tests/unit/test_slice_config_consistency.py`.

Docs: `docs/guide/02-how-a-research-runs.md`, `04-config-map.md`, `06-known-problems.md` (new 6.5),
`docs/architecture/loops.md` (regenerated by `scripts/gen_loops.py`).

## 3. The lease logic (F5), for the record

* `lease_ttl_s` stays **1800 s**. `tests/unit/test_slice_config_consistency.py` pins "a provider
  lease outlives a slice" (Tolmar 2026-10-21: a long call must not be re-leased by the next
  slice, which makes the old call look abandoned and pays it twice). A first draft shortened the
  ttl to 120 s; it was dropped because a live worker that stalled for two minutes would have
  changed epoch and re-issued its own calls.
* New: `lease_heartbeat_s: 20` (the runner renews while the graph runs) and
  `lease_stale_after_s: 90`: another worker may take a lease its owner has not renewed for 90 s.
  Cloud Tasks (`runs` queue: 5 attempts, 10 s backoff, doubling) retries a failed delivery at
  about +10, +30, +70 and +150 s after the death: the first three answer 503 (busy), the last
  takes the lease (60 s of margin). The epoch moves on at a takeover, so the dead owner's
  unfinished calls are stale and are re-issued once, flagged as possible duplicates (the existing
  rule). A live owner is never taken from (4 missed beats needed). An owner that stalls for more
  than 90 s and then finds its lease gone stops the slice (`LeaseLost`).
* The retries consume the queue's 5 attempts. If the owner wants more margin: `max_attempts = 8`
  on the `runs` queue in `infra/stack/main.tf` (not changed; `terraform apply` is the owner's).

## 4. Evidence

Commands (from `/home/sakib/ABM Generator`, own scratch DB, `-m "not eval and not live"`):

```
TEST_DB=ai_test_d1 PGPASSWORD=dev APP_RW_PASSWORD=... MIGRATOR_DATABASE_URL=postgresql://postgres:dev@localhost:5432/ai_test_d1 \
  .venv/bin/pytest <dirs> -m "not eval and not live" -q -p no:cacheprovider
```

* Session 1 (before the lease redesign and the pause fixes below): `tests/agents tests/workflow
  tests/runs tests/replica tests/parallel tests/connectors tests/jobs tests/people tests/tracker
  tests/spend tests/unit`: 2671 passed, 1 xfailed, 1 failed. The failure was
  `tests/unit/test_slice_config_consistency.py::test_a_provider_lease_outlives_a_slice`, which pinned
  the 1800 s ttl that my first draft of F5 had shortened: the design was changed (section 3), the
  test is unchanged and passes.
* Session 2 (final code): the same directories plus `tests/system tests/integration tests/worker
  tests/api tests/crosscut tests/guardrails tests/isolation`: **3798 passed, 1 xfailed, 1 failed** in 27
  minutes. The failure was `tests/system/test_single_write_paths.py` (`research_jobs` is written
  only by `replica/credits.py`; my recovery module wrote it). The write moved to
  `replica/credits.py requeue_unstarted`; the re-run of that file, `tests/runs/test_recovery.py`,
  `tests/system/test_static_guards.py`, `tests/replica/test_credits.py` and
  `tests/replica/test_engine.py` passes (39 tests).
* Not run (outside what I touched): `tests/exports`, `tests/documents`, `tests/grading`,
  `tests/llm`, `tests/obs`, `tests/privacy`... and the rest of the suite; you run the whole suite.

Tests that fail on the old code (overlay with the previous `graph.py`, `wiring.py`, `runner.py`,
`lifecycle.py`, `jobs.py`, `auto_resume.py`, DB `ai_test_d1b`; the failing test ids were kept):
24 of 36 in `tests/agents/test_orchestration_fixes.py` + `tests/agents/test_failure_paths.py`
fail. The 12 that pass are: a non-retryable pipeline error still fails only its pipeline, a defect
in `deliver` is not retried, the two detections-writer tests and the page-read ceiling test (their
code is in `pipelines.py` / `jobs/adapters.py`, which the overlay keeps new) and the 7 older
`test_failure_paths` tests. 30 of 43 in `tests/workflow/test_orchestration_runner.py`,
`tests/runs/test_orchestration_db.py`, `tests/runs/test_recovery.py`, `tests/replica/test_engine.py`
and `tests/replica/test_auto_resume.py` fail (many because the function they test did not exist:
that is the finding); the 13 that pass are the recovery tests (the module is new and the overlay
keeps it) and tests of behaviour that was already right (an auto-resume that must not continue a
provider outage, the early-return paths of the runner).

Static checks: `ruff check src tests` passes; `mypy src` passes (703 files);
`lint-imports` 19 contracts kept; `interrogate` passes; `scripts/gen_loops.py --check`,
`scripts/wiring_audit.py --check`, `scripts/check_route_catalog.py` pass after regeneration of
`docs/architecture/loops.md` (the line numbers it lists move with every edit: regenerate once more
after the last change). `scripts/lint_migrations.py` not run: no migration.

## 5. Open question 3 of the review (ledger "started" attempt), what I found

Confirmed by reading `connectors/ledger.py claim`: a call cancelled by a ceiling or a slice timer
leaves a `started` attempt. An identical claim by the same worker in the **same lease epoch** (the
same slice) finds it not terminal and not yet `in_flight_stale_s` old and answers `InFlight`, so
the caller waits up to `in_flight_wait_s` (1200 s with the caps off) for a call that no longer
exists. That is the "pipeline re-issues the same keys" hand-off after a row ceiling in the same
slice, and it is why `pipelines.jobs` can take 18 minutes. In a later slice (a new epoch after the
lease release) the attempt is closed `abandoned`, charged its reserve again and flagged
`possible_duplicate`. Small fix, not done because it changes the paid-call ledger: track the
attempts this process really has in flight and close any other `started` attempt of the same owner
and epoch `abandoned` at once (no wait, and no second reserve for a call that never reached the
provider). The new ceilings of F9 add no new case: a pipeline call cut off at 300 s is not retried
in the same slice.

## 6. Owner decisions needed

1. `max_attempts` of the `runs` Cloud Tasks queue (5 today) and `lease_stale_after_s` (90 s): the
   recovery of a dead worker's slice rests on the last retry at about +150 s. Raising the queue to
   8 attempts widens the margin (infra change).
2. Should a **provider** outage that outlasts the node retries (`RateLimited`, 5xx) be continued
   automatically like `model_unavailable`? Infrastructure blips are (`INFRASTRUCTURE_STOPS`),
   provider outages are not.
3. Should `slice_ceiling` (now PARTIAL, results kept) be auto-resumed? Not added to `ELIGIBLE`.
4. The text shown for `token_guard_stopped`, `no_progress` and `entity_mismatch` is missing in
   `replica/workspace.py STOP_TEXT` (D2's file); `slice_ceiling` has one.
5. The ledger fix of section 5.
6. Hand-off recovery repeats named Cloud Tasks; a repeat whose original task already ran can land
   up to an hour late (Cloud Tasks keeps the name). Unique repeat names would remove that delay
   at the price of a possible duplicate delivery (all deliveries are idempotent), your call.
7. The approval-timeout task has one name per run, so a second pause of the same run never gets
   its own task (the name exists); the sweeper's wait expiry now covers it (15 minutes after the
   deadline).

## 7. Not done / limits

* F8 belongs to D2 (workspace listing).
* F12 second half (see the table).
* No test exercises Cloud Tasks itself; the retry timings in section 3 are from
  `infra/stack/main.tf`, not measured.
* The new ceilings and the lease rule change what the worker does after a failure; none of it has
  run on staging. Deploy needs the owner's decision (nothing is committed).
