# 03 Research run lifecycle

Verified against the code on 2026-11-03 (working tree of `feat/final-product`, uncommitted changes included).

See also: [index](README.md) · [01 architecture](01-architecture-and-planes.md) · [02 request path](02-request-path-and-auth.md) · [04 model routing](04-model-routing.md) · [05 spend and caps](05-spend-and-caps.md) · [06 data and isolation](06-data-and-isolation.md) · [07 documents](07-documents-and-sandbox.md) · [08 exports and gate](08-exports-and-gate.md) · [09 ops](09-ops.md) · [10 known gaps](10-known-gaps.md)

`src/account_intelligence/workflow/runner.py` and `src/agents/production/wiring.py` changed while this page was written, so they are mostly cited by function name.

## Two ways a run starts

**A. `POST /v1/research` (what the web app's "Research a new company" uses).** Confirm reserves credits and creates a `new_research` job (`src/account_intelligence/api/routes/research.py:12-17`, `CR.reserve` at `:172`), then `jobs.run_or_enqueue` (`:210`) creates the named Cloud Task `job-{id}` (`src/account_intelligence/replica/jobs.py:430-449`, `src/account_intelligence/platform/gcp/adapters.py:191-197`). The worker's `POST /tasks/research-job` (`src/account_intelligence/api/routes/tasks.py:218-229`) calls `run_job`: claim (idempotent: a job not `queued` is skipped), run the engine, settle when the run ends (`src/account_intelligence/replica/jobs.py:380-416`). In staging/production the engine is `graph` (`src/account_intelligence/replica/jobs.py:317-326`): `GraphEngine` creates an `account_runs` run through `lifecycle.create_run` with `run_input.job_id`, scope rows, `expected_tokens`, topic, goal and models, then enqueues slice 0 (`src/account_intelligence/replica/jobs.py:157-264`, create at `:229`, enqueue at `:253`).

**B. `POST /v1/runs` (direct API, `Idempotency-Key` header).** Input mode `file` goes to `create_file_run` (`src/account_intelligence/api/routes/runs.py:121-125`); otherwise one transaction adds the account, consumes plan limits and calls `lifecycle.create_run` (both run rows, credit reservation, seeded tracker), then enqueues slice 0 after commit (`src/account_intelligence/api/routes/runs.py:113-195`). Same key + same body replays; same key + other body is 409; no credits 402 (`src/account_intelligence/api/routes/runs.py:3-5`).

`create_run` stamps `run_input.replay_key_version` on every run (`src/account_intelligence/runs/lifecycle.py:224-226`; page 05).

## Which graph runs a slice

`POST /tasks/run-slice` decides per slice (`src/account_intelligence/api/routes/tasks.py:113-149`):

1. `is_file_run` (input mode `file`, `src/documents/graph.py:453`) -> `FileGraphFactory` (`src/documents/graph.py:407`): setup, extract, tracker, internal file, gate, deliver, finish; no paid data-broker call (`src/documents/graph.py:1-25`). 503 when documents are not configured (`src/account_intelligence/api/routes/tasks.py:128-131`).
2. else `uses_abm(deps)` (`src/agents/production/wiring.py:339`) -> `AbmGraphFactory` (the ABM agent graph). `uses_abm` is True for `run_mode` `pull`/`build`, for a company run whose `run_input` has `job_id` or `linked_file_run`, and otherwise returns `abm_flag_on` (`src/agents/production/wiring.py:369`): **True when no `abm_agents` rule was ever written** (ADR-0135); an explicit rule decides through `admin.flags.evaluate` (`src/admin/flags.py:24-34`), which is deny-by-default for every other flag.
3. else no factory: `execute_slice` builds Workflow v1 (`_workflow_graph` in `runner.py`) from `config/workflow_v1.yaml`.

So every run started through path A (a job) runs the ABM graph, and so does a path-B company run unless an explicit `abm_agents` rule leaves the tenant out. When Workflow v1 does run, `_slice` logs `workflow_v1.deprecated` once at slice 0 (`src/account_intelligence/workflow/runner.py`, `_slice`); v1 removal is scheduled for the release after next (`docs/decisions/ADR-0135-workflow-v1-deprecated.md`).

## One slice (`execute_slice` in `runner.py`)

- In-process mutex `_IN_FLIGHT`, then the run lease (`ledger.acquire_lease`); another live owner -> `busy` (503, Cloud Tasks redelivers) or `noop` for a stale copy (`execute_slice`, `_yield`).
- `_begin`: terminal -> `noop`; AWAITING_APPROVAL -> `awaiting`; `begin_slice` counts the slice; past `max_slices` (pause slices not counted) ends `slice_ceiling`; then G12 `deps.ensure_independent_judge()` before any paid call, ending `judge_not_independent` (ADR-0118, page 04). Both end through `_end_stopped`: PARTIAL if anything usable, else FAILED with full refund.
- The graph runs under `asyncio.timeout(slice_seconds)`; `_drive` re-reads status every `CANCEL_POLL_S` (2 s) for cancel and renews the lease every `lease_heartbeat_s`; a lost lease raises `LeaseLost` and the slice stops.
- Time out -> release lease, enqueue `slice + 1` (named task `run-{id}-slice-{n}`, `src/account_intelligence/platform/gcp/adapters.py:162-172`). `max_no_progress_slices` (3) unmoved slices in a row end `no_progress` (`_timed_out`); this stays on with caps off (`src/account_intelligence/app/container.py:436-437`).
- Interrupt -> AWAITING_APPROVAL with a timeout task (`_end`).
- Unexpected error -> `_stop_unexpected`: PARTIAL with the error class as reason if usable, else FAILED + refund.
- Slice settings come from `config/workflow_v1.yaml` for **every** graph: `slice_seconds: 1650`, `max_slices: 16`, `max_no_progress_slices: 3` (`config/workflow_v1.yaml:13-19`), loaded by `_slices` (`src/account_intelligence/app/container.py:424-438`); caps off makes `max_slices` unbounded (`:434`).

## Leases (ADR-0131)

- A claim does NOT renew the lease: `claim` and `is_replayable` call `_held_lease`, which locks the row `FOR UPDATE` (the run mutex) and writes nothing when the lease is this worker's and live; any other case goes through the old upsert (`src/account_intelligence/connectors/ledger.py:6-12`, `:281-300`).
- Renewal is the heartbeat only: `acquire_lease` upsert (`src/account_intelligence/connectors/ledger.py:312-315`) every `lease_heartbeat_s: 20`; a lease unrenewed for `lease_stale_after_s: 90` may be taken; TTL 1800 s (`config/providers.yaml:36`, `:46-47`).
- At most `max_in_flight_claims: 4` open claim transactions per worker event loop (`config/parallel.yaml:135`, semaphore `_claim_gate` at `src/account_intelligence/connectors/ledger.py:302-310`).

## Ending: the tracker verdict owns COMPLETED (ADR-0128)

`_end` -> `_finish` (runner) -> `lifecycle.finish`. `finish` evaluates `tracker.verdict` exactly once, maps it with `terminal_status`, sweeps open rows for non-COMPLETED ends, calls `finish_run(verdict=...)`, then settles once (`src/account_intelligence/runs/lifecycle.py:739-776`). `terminal_status`: COMPLETED only when `verdict.can_complete`, else PARTIAL; `finish_run` then stores reason `tracker_verdict` and `blocked_by: k1, ... and N more` in `stop_detail` (`src/account_intelligence/tracker/guard.py:32-47`, `:58-107`, reason set at `:77`). `tracker_verdict` is a finished-with-gaps reason with client text (`src/account_intelligence/replica/workspace.py:582`, `:804-810`). Database triggers from migration 0010 also refuse a bad COMPLETED (`src/account_intelligence/tracker/guard.py:9-11`).

## The sweeper: six jobs

`sweep_pass` (`src/account_intelligence/api/main.py:240-283`) runs, each in its own `try` so one failure never stops the next:

1. `_sweep_documents` - re-enqueue uploads whose worker died mid-step, plus a small deliverable-import backfill (`src/account_intelligence/api/main.py:211-237`, `:245`).
2. `sweep_once` - `runs.sweeper.sweep`: runs with a lease dead for `orphan_after_s` (default 600 s, `src/account_intelligence/config.py:345-347`) end `worker_lost` (`:249`).
3. `_mark_stalled` - running runs whose `progress_at` has not moved for `stall_after_s` are marked once and the owner told (`:262`; `src/account_intelligence/runs/sweeper.py:161-180`).
4. `_auto_resume` - `replica.auto_resume.auto_resume_pass` (`:269`): at most `MAX_AUTO_RESUMES = 5` per chain within `WINDOW_HOURS = 24`, only `ELIGIBLE` stops, only job-started runs (`src/account_intelligence/replica/auto_resume.py:12-25`, `:64-66`, `:108`).
5. `_close_ended_jobs` - settle jobs still `queued`/`running` whose run ended (`src/account_intelligence/api/main.py:312-353`, `:275`).
6. `_recover_handoffs` - `runs.recovery.recover`: re-hand queued jobs, runs that never began slice 0, waits past their deadline (`src/account_intelligence/api/main.py:286-309`, `:281`; `src/account_intelligence/runs/recovery.py:1-25`).

The list is documented in `src/account_intelligence/runs/sweeper.py:20-31` and checked by `tests/runs/test_sweeper_jobs.py`. It runs (a) in a background loop on a warm worker every `sweep_interval_s` (300 s) (`src/account_intelligence/api/main.py:356-366`, `src/account_intelligence/config.py:348`) and (b) on the Cloud Scheduler tick `POST /tasks/sweep` every 5 minutes, because the worker scales to zero (`infra/stack/main.tf:810`, `src/account_intelligence/api/routes/tasks.py:392-404`). There is no retention job (`src/account_intelligence/runs/sweeper.py:33-35`).

## stall_after_s (ADR-0132)

`STALL_AFTER_S_DEFAULT = 1800`, used by `DEFAULTS` when `platform_defaults` has no row (`src/account_intelligence/connectors/spend_guard.py:91-97`, `:100-115`); the column default is also 1800 (`migrations/0067_resumable_research.sql:19`). Every ledger event and model call moves `progress_at` (`src/account_intelligence/connectors/spend_guard.py:16-18`).

## Scheduled refresh tick bounds (ADR-0108, ADR-0132)

`POST /tasks/schedules-tick` every 15 minutes (`infra/stack/main.tf:806`) calls `schedules.tick` with `run_or_enqueue` (`src/account_intelligence/api/routes/tasks.py:258-270`). One `StartBudget` (`src/account_intelligence/replica/schedules.py:159-188`) from `platform_defaults.schedule_tick_max_starts` (default 10) and `schedule_tick_max_per_tenant` (default 3, at most the overall) (`src/account_intelligence/replica/schedules.py:202-212`, `migrations/0098_schedule_tick_bounds.sql:13-18`) covers both phases: due refreshes in `fair_order` round robin (`:191-199`), then promoted waiting jobs with `promote_waiting(limit=room)` (`:300-321`). Over-ceiling accounts are left due and counted `deferred` (`:215-229`).

## Code vs ADR

- ADR-0132 says the sweeper is "`runs/sweeper.py`'s ... `sweep_pass`"; `sweep_pass` and the job functions live in `src/account_intelligence/api/main.py:240`; `runs/sweeper.py` only documents the list and holds `sweep`/`mark_stalled`. Six jobs confirmed.
- `SliceSettings` comments in `runner.py` still say "240 s slices, at most 60" and "max_slices 8 on 2026-10-22"; the config is 1650 s / 16 (`config/workflow_v1.yaml:13-14`).
- `CloudTasksScheduler`'s docstring says "a 1200 s slice"; `SLICE_DEADLINE_S` is 1800 for 1650 s slices (`src/account_intelligence/platform/gcp/adapters.py:94`, `:99`).
- `config/providers.yaml:37-38` still says "every paid-call claim renews it too", which ADR-0131 removed (`src/account_intelligence/connectors/ledger.py:9`).
- The runner docstring's step 5 says COMPLETED is refused "if a row is open"; since ADR-0128 the tracker verdict policy decides (non-High gaps can complete as `complete_with_gaps`).
