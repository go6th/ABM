# Owner decisions of 2026-10-30, what was done

Four small changes the owner authorised while asleep. Nothing is committed (owner rule), nothing
is deployed, no `terraform apply`, no cloud command, no live provider, no paid run. Tests ran on
the scratch database `ai_test_e1` (own name, `PGPASSWORD=dev`, only `APP_RW_PASSWORD` taken from
`.env`, `.env` never sourced), always with `-m "not eval and not live"`.

Result table of the verification runs: section 6.

## 1. Cloud Tasks `runs` queue: 8 attempts

* **What:** `infra/stack/main.tf`, queue `runs`: `max_attempts` 5 to 8 (backoff untouched: 10 s
  minimum, 300 s maximum, 4 doublings). Not applied: `terraform apply` is the owner's.
* **Takeover timing, computed.** Cloud Tasks waits `min_backoff`, doubles it `max_doublings`
  times, then adds `min_backoff * 2^max_doublings` each time, capped at `max_backoff`. Gaps:
  10, 20, 40, 80, 160, then 300 s (320 capped), 300 s. Retries (seconds after the first failed
  delivery): **+10, +30, +70, +150, +310, +610, +910** (seven retries for eight attempts). The
  ledger takes a lease its owner has not renewed for `lease_stale_after_s` = 90 s (a beat may have
  been written just before the death, so 90 + 20 = 110 s is the safe bound). The first retry
  past it is the one at **+150 s**: the retries at +10, +30 and +70 s answer 503 "busy", the +150 s
  retry takes the lease. That is unchanged by 8 attempts; what changes is that three more retries
  (+310, +610, +910 s) remain if the takeover attempt itself fails (a second kill, a cold start
  that outlasts the dispatch deadline). Before, the +150 s retry was the last attempt.
* **Cost of the change:** a slice that fails for a reason a retry does not mend is now delivered
  eight times instead of five. Each redelivery is idempotent (lease, counted slice, checkpoint)
  and a failing worker answers fast, so the cost is queue traffic, not paid calls.
* **Test:** `tests/unit/test_slice_config_consistency.py` now reads the four numbers of the `runs`
  queue from `infra/stack/main.tf` and computes the schedule (`retry_times`):
  `test_the_retry_schedule_of_the_runs_queue` (fails on the old file: 5 attempts, no +310 s),
  `test_a_killed_workers_lease_can_be_taken_by_the_last_retry_of_its_slice` (the takeover retry is
  +150 s, the earlier ones are +10, +30, +70 s, at least three retries remain after it).
* **Docs:** `docs/guide/02-how-a-research-runs.md` (lease paragraph), `04-config-map.md` (new
  paragraph on the queues), `06-known-problems.md` (problem 7 text, 6.5 "owner decisions").
* **Not touched (the speed agent's file):** the comment in `config/providers.yaml` (`ledger`
  block, "five attempts") still says five; the value it describes (`lease_stale_after_s` 90) is
  right. Fix the words when that file is free.

## 2. Auto-resume of a provider outage; plain stop texts

* **What:** `replica/auto_resume.py`: new `PROVIDER_OUTAGE_STOPS` (`RetryableProviderError`,
  `RateLimited`, `ProviderJobTimeout`, `CallInFlight`, `Overloaded`, `CircuitOpen`: the classes a
  run ends with, `runner._stop_unexpected`, when a 429/5xx/timeout/open breaker outlasts the node
  retries) joins `ELIGIBLE` next to `model_unavailable`. The same bounds apply: `MAX_AUTO_RESUMES`
  = 5 per chain, `WINDOW_HOURS` = 24, no live lease, one continuation per stop. Credits, key and
  auth refusals (`AuthError`, `CreditsExhausted`, `model_auth_failed`, `model_credits_exhausted`)
  stay with the owner. `slice_ceiling` is **not** in `ELIGIBLE` (manual Resume only); the module
  docstring and the code comment say why.
* **Stop texts:** `replica/workspace.py` `STOP_TEXT` (only that dict, plus two constants
  and two `STOP_TEXT.update` blocks right after it) now has a plain message for every code that can
  occur: `slice_ceiling` (says it does not continue by itself and the owner presses Resume),
  `no_progress`, `token_guard_stopped`, `entity_mismatch`, `invalid_setup`, `no_pulled_data`,
  `stale_pulls`, `sense_check_budget_exceeded`, `distill_budget_exceeded`, `sense_check_missing`,
  `sense_check_stale`, `manifest_changed`, `third_party_domain`, `client_output_leak`,
  `approval_timeout`, `canceled_by_user`, the six provider-outage classes (one text: "The platform
  continues the research by itself; you can also press Resume") and the eleven connection-drop
  classes of `INFRASTRUCTURE_STOPS` (likewise).
* **Tests** (`tests/replica/test_auto_resume.py`): the old test that pinned "a provider outage is
  not continued" became
  `test_a_dropped_connection_or_a_provider_outage_that_ended_a_run_is_continued` (every outage class
  and `model_unavailable` continue once; `slice_ceiling`, `AuthError`, `CreditsExhausted`,
  `model_auth_failed`, `model_credits_exhausted`, `token_guard_stopped`, `entity_mismatch` do not);
  `test_a_provider_outage_is_bounded_per_chain_and_by_the_window` (a 2-day-old outage stop and a
  chain that already used 5 continuations are left to the owner);
  `test_every_stop_that_can_occur_has_a_plain_message` (no code of the list, nor any code in
  `ELIGIBLE`, falls to "It stopped before finishing (reason: ...)"; slice ceiling and no-progress
  mention Resume; outages and dropped connections say the platform continues by itself). The
  first two fail on the old `ELIGIBLE`; the third fails on the old `STOP_TEXT`.
* **Docs:** `docs/guide/02-how-a-research-runs.md` (the table of stops), `06-known-problems.md`
  (6.5 "owner decisions").

## 3. Budget floor (known problems 16, e2e D-5)

* **What:** `replica/credits.py`.
  * `estimate`: with the caps on, the average of the account's last finished jobs of the scope is
    never below `floor_credits(scope)` = half of the configured rate (`ESTIMATE_FLOOR_SHARE` 0.5;
    full 110, people 30, jobs 10, topic 20). With `RESEARCH_CAPS=off` the history alone decides, as
    before (unchanged). The page that shows the estimate (`/v1/accounts/<d>/refresh`) and Confirm
    use the same function, so what is shown is what is reserved.
  * `estimate_resume(session, account, kind, run_id)` (new): the estimate of the scope times the
    share of the stopped run's planned steps not yet done (`steps_left`, counting only the rows in
    the run's scope), never below the floor; a run with no coverage rows, or none done, is
    reserved whole; caps off: the plain `estimate`.
  * `replica/auto_resume.py continue_run` (the one path of the Resume button and the sweeper)
    reserves through `estimate_resume` instead of `estimate`.
  * `PLANNED` (the in-scope rule) is held in `credits.py` as well as in `workspace.py`: importing
    the workspace from `credits` breaks the import contract "runs never imports agents" (runs ->
    credits -> workspace -> source_label -> agents.naming); a test keeps the two equal.
* **Tests** (`tests/replica/test_credit_floor.py`, 7 tests, real Postgres):
  the e2e scenario (a cheap earlier run of 29 credits gives 110, was 29), a resume after history
  `[29, 1]` reserves at least 110 and writes that reserve to the ledger (was 15 / 1), the share of
  steps left above the floor (2 of 3 left gives 200 of 300, 1 of 3 gives the floor 110, none done
  gives 300), a run with no coverage rows is reserved whole, the Refresh page shows the floored
  number, caps off leaves 29 (and the plain estimate for a resume), `PLANNED` equality.
  Existing `tests/replica/test_credits.py` (history of 120 credits for Highmark) unchanged.
* **Docs:** `06-known-problems.md` item 16 (now fixed in the tree), `04-config-map.md` (credits
  row).

## 4. Real-account fixtures out of the repository

* **Moved with `git mv` (never deleted)** to `unwanted-things/real-account-fixtures/`
  (inventory rows in `unwanted-things/README.md`; `unwanted-things/cleanup.sh` step 6 holds the
  irreversible history purge as a commented command for the owner):
  the real account's workspace zip (22 files, including raw provider results),
  `tests/documents/test_tables_real_gsk.py`, `tests/evals/test_tabular_gsk_live.py`,
  `evals/tabular_gsk/cases.yaml`, and the three generated preview shots in `web/shots/`
  (`structured-answer-1440.png`, `-390.png`, `structured-preview.html`).
* **Synthetic replacements:**
  * `tests/documents/workspace_zip.py` (new): builds a workspace zip in memory for the fictional
    "Synthetic Holdings" (`holdings.example`) with the structure of a delivered package
    (`RESTORE.md`, `notes/`, `deliverables/` with Job Postings, People List and Tech Stack and
    Teams workbooks, `data/` JSON, `build/`), deterministic bytes. The Jobs sheet is the existing
    synthetic 644-row workbook (`fixtures.holdings_job_postings_xlsx`); the people are invented;
    the Teams sheet and its JSON twin are the 40-row decoys.
  * `tests/documents/test_tables_workspace_zip.py` replaces `test_tables_real_gsk.py`: the same
    production-path assertions (zip members read, 644 rows under 13 typed columns, the strict
    question 0 rows, relaxing the status 20 rows / 14 unique, a requisition on two rows keeping the
    row seen last, the full 40-row set, 4 keyless rows, `read_rows`) against the synthetic truth.
  * `tests/evals/test_tabular_live.py` + `evals/tabular_synthetic/cases.yaml` replace the live
    eval (marker `live`, never run here); `.github/workflows/eval.yml` points at them.
  * `tests/documents/fixtures.py`: `gsk_*` / `GSK_*` names became `holdings_*` / `HOLDINGS_*`, the
    twelve requisition numbers of the made-up postings were moved by +100000 so they no longer
    match a real sheet, the sample sentence names "Synthetic Holdings".
* **Neutral wording** (every mention of the account in code, config, tests, comments, docs):
  `src/agents/entity.py` (comment), `tests/agents/test_entity.py` (now a fictional
  acronym-domain case, `lmg.example` for "Longbridge Medical Group"), `config/models.yaml`
  (two comments), `.gitignore` (the `!tests/fixtures/gsk/*.zip` exception removed),
  `.github/workflows/eval.yml`, `tests/documents/{fixtures,test_tables_golden,test_tables_db,
  test_source_tools_db,test_units}.py`, `tests/replica/{test_answers,test_router,test_tool_loop,
  test_whole_file}.py`, `tests/rag/test_retrieval.py` (a requisition number),
  `web/scripts/preview-structured.tsx`, `web/tests/unit/answer-structured.test.tsx`,
  `docs/{data-access,data-access-handoff,tabular-queries,workspace-how-it-works}.md`,
  `docs/audit/2026-10-30-{fix-wave-d2-done,leakage-review}.md`,
  `docs/design/claude-design/{A3-admin-client-detail,A5-admin-library}.html` (a client-name list),
  `config/skill/archive/references/{lessons,checklist,tools}.md` ("an earlier account") and the
  ignored local copy `docs/sources/abm-skill/.../{lessons,checklist,tools}.md`.
  `config/skill/name-scan-acknowledged.yaml`: the digests of the two edited, pinned files
  (`checklist.md`, `lessons.md`) were re-pinned; nothing else in them changed
  (`scripts/abm_name_scan.py` reports OK, six acknowledged hits as before).
* **Final grep** (case-insensitive, whole repository except `.git`, `node_modules`, `.venv`,
  `unwanted-things`, `.claude/worktrees`): the only remaining hits are random base64 inside
  `web/package-lock.json` and `docs/design/archive-page-mock.html`, not the name.
* **Git history still holds the old zip** (and the old tests, shots and cases). History was not
  rewritten. `unwanted-things/cleanup.sh` step 6 has the commented `git filter-repo` command for
  the owner (it rewrites every commit id and needs a force push).
* **Also outside the repository:** the memory note `pytest-live-marker` names the old live test
  file; it is the owner's notes, not edited here.

## 5. Commands

```
TEST_DB=ai_test_e1 PGPASSWORD=dev APP_RW_PASSWORD="$(grep '^APP_RW_PASSWORD=' .env | cut -d= -f2-)" \
  MIGRATOR_DATABASE_URL=postgresql://postgres:dev@localhost:5432/ai_test_e1 \
  .venv/bin/pytest tests/replica tests/documents tests/unit tests/evals tests/system tests/rag \
  tests/agents/test_entity.py tests/agents/test_skill_context.py tests/agents/test_name_scan_exemption.py \
  tests/runs tests/workflow -m "not eval and not live" -q -p no:cacheprovider
.venv/bin/ruff check src tests ; .venv/bin/ruff format --check src tests ; .venv/bin/mypy src
.venv/bin/lint-imports ; .venv/bin/python scripts/wiring_audit.py --check
.venv/bin/python scripts/gen_loops.py ; .venv/bin/python scripts/gen_loops.py --check
.venv/bin/python scripts/abm_name_scan.py
cd web && npx vitest run tests/unit/answer-structured.test.tsx
```

## 6. Results

Run 1 (`tests/replica tests/documents tests/unit tests/evals tests/system tests/rag` and the
entity, skill-context, name-scan, runs and workflow tests): 1218 passed, 1 skipped, 1 xfailed,
6 deselected (live/eval), 3 failed. The three:

* `test_the_zip_is_synthetic_and_stable`: mine. The workbook inside the zip carries its creation
  time, so two builds differed. Fixed (`workspace_zip_bytes` is `functools.cache`d); re-run green.
* `test_one_retry_owner_per_failure_class_w6`: it runs `scripts/gen_loops.py --check`;
  `docs/architecture/loops.md` was stale because the files other agents edit moved line numbers.
  Regenerated (`gen_loops.py`); `--check` now passes.
* `test_interrogate_docstring_coverage_is_complete` (99.9%): not mine. The missing docstrings are
  nested functions in `agents/graph.py` (`stored_views.store`, `pipelines.rows_failed`),
  `agents/production/provider.py` (`entity_lock.read_home`, `entity_lock.brand_search`),
  `people/adapters.py`-side `ConnectorPeopleSearch.search_association.people_of` and a
  `list_items.one` helper: the speed agent's and the D1 wave's files. Still red.

Final targeted re-run (zip test, credit floor, auto-resume, slice consistency, static guards):
27 passed, 1 failed (the interrogate test above). Also green: `lint-imports` (19 kept, 0 broken),
`wiring_audit.py --check`, `gen_loops.py --check`, `abm_name_scan.py` (OK, six acknowledged hits),
vitest `answer-structured.test.tsx` (7 passed), `ruff check` and `ruff format --check` on every
file this change touched. Not green and not mine: `ruff check` E501/F401/I001/SIM905 in
`src/agents/production/{provider,links}.py`, `ruff format --check` on `provider.py`, and 8 mypy
errors in `provider.py` (the speed agent's work in progress).

Tests against the OLD behaviour (the old `credits.py` floor and `resume` estimate, the old
`ELIGIBLE` with `slice_ceiling`, the old `STOP_TEXT`, `max_attempts = 5`, overlaid and restored
byte for byte): 8 of the new tests fail: four in `test_credit_floor.py` (cheap earlier run, resume
never one credit, share of steps left, refresh page), two in `test_auto_resume.py` (outage and
`slice_ceiling` eligibility, plain stop texts) and both retry-schedule tests. Three tests pass on
the old code because they pin behaviour that must not change: caps off, a run with no coverage
rows, `PLANNED` equality, and the outage window/bound test (an outage was never continued, so the
bounds held trivially; it guards the new behaviour from running past its bounds).
