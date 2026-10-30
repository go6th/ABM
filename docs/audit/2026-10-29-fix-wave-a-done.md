# Fix wave A: pipeline review findings for the job postings path (2026-10-29)

Source: `docs/audit/2026-10-29-review-pipeline.md`. Scope: the job postings pipeline, its
incremental re-research, the Job Postings reader and the feed arguments. Nothing was committed,
no server or paid run was started, no file was deleted. Data in tests is synthetic (`.example`).
No migration was needed: finding 14 keeps its date in the stored posting record (`row_json`), so
`EXPECTED_REVISION` and the migrations README were not touched.

## Per finding

| # | Result | What changed | Tests |
|---|--------|--------------|-------|
| 1 | fixed | `JobsPipeline._active` returns no set when the active pull is cut short (`batch.complete` false): statuses stay, the pull row is marked cut. `_carry` never closes by absence a posting only the live board holds (no feed id) or one stored without a description (`_absence_is_silent`). The active call no longer sends `with_description_only`. | `test_a_cut_short_active_pull_closes_nothing`, `test_absence_does_not_close_a_board_only_or_a_description_less_posting` (both fail with the old `_active`: shown by re-creating it) |
| 2 | fixed | `previous_delivered_run` takes only a run whose stored pass has `exit_reasons.pull == converged`, `pull_complete` and no `invariant_error`. The pipeline gets the delta bound as `delta_since` and passes it to `pull_window(..., since)` only when it holds the loaded previous record; `pipelines.run_jobs` loads the previous record first. New record fields `pull_complete`, `lanes_off`, `lexicon_hash`. | `test_the_delta_bound_is_applied_only_when_the_previous_record_was_loaded`, `test_a_research_that_did_not_pull_its_whole_feed_is_not_a_base` |
| 3 | fixed | A carried posting cannot be graded again (the engine verifies only against this run's ledger). The previous run's current graded `jobs.*` fact for the same attribute, value and address is recorded again for the new run through the engine's one write path (`grading.repo.record_graded_fact`), same grade, evidence, lane, event date. Only facts graded under the current grading configuration are carried; the rest are counted (`jobs_carried_claims_without_fact`). The unreadable-carried-text `continue` is now counted (`claims_skipped_text`, run counter `jobs_claims_without_text`). New module `src/agents/production/jobs_carry.py`; `claims.claim_id` and `claims.claimable` added. | `test_a_carried_postings_graded_finding_is_recorded_again_for_the_new_run`, `test_the_claim_id_ties_a_claim_to_its_posting_and_platform` |
| 4 | fixed | `jobs.export.is_exported` adds `window_bucket != out_of_window`: the file's rows, the counts (`compute_counts` already left them out) and the Tech ranking agree. A carried posting past the window stays stored. Summary paragraph 1 says so. | `test_a_carried_posting_past_the_window_is_stored_but_not_exported`, `test_a_posting_past_the_window_is_not_exported` |
| 7 | fixed | Previous run ordered `j.as_of DESC, j.created_at DESC`, joined to `account_runs` with a terminal result status (COMPLETED, PARTIAL, BUDGET_EXCEEDED) and delivered files. | `test_the_base_is_the_newest_research_not_the_newest_rebuild`, `tests/jobs/test_repo_db.py::test_incremental_fields_round_trip...` (a run that has not ended is not a base) |
| 8 | fixed | `_merge_same_key` after the fan-out: one posting per key, URLs and call references unioned. | `test_two_fresh_records_that_resolve_to_one_stored_posting_give_one_posting` (fails with the merge removed: shown) |
| 9 | fixed | `FeedArgs` leaves `active_only` and `with_description_only` out of the serialised arguments while they hold their default, so the ledger key of every unchanged request is the pre-flag key. `with_description_only` now defaults to false and no caller sends it, so the first pull is the whole window; `PipelineResult.description_less` counts feed postings that came without a description (run counter `jobs_description_less`). | `test_the_new_feed_filters_leave_the_ledger_key_of_an_unchanged_request_alone`, `test_postings_that_came_without_a_description_are_counted_in_the_pass_record`, `tests/connectors/test_contracts.py::test_signals_requests_carry_page_limit_and_delta_filter` |
| 10 | fixed | A title candidate past the extractor budget is `unclassified` (`classified_by none`, reason `cap`): a gap pass retries it and the file shows it unclassified, not a final heuristic class. | `tests/jobs/test_pipeline.py::test_caps_bound_escalation_and_extractor_calls` (rewritten) |
| 11 | fixed | Summary paragraph 3 states that a class decided by reading the whole posting is a graded finding and a keyword-rule class is a class only; two count lines on the Counts sheet, "classed by reading the whole posting (graded findings)" and "classed by a keyword rule (no finding)", sum to the rows of the file. `PostingRow.classified_by` added. | `test_a_carried_posting_past_the_window_is_stored_but_not_exported` (the two lines sum to the rows) |
| 12 (jobs) | fixed | A failing `store.put` while archiving a snapshot is logged and that posting falls back to its stored text entry; `save_run` carries on. People and Phase 1 archive paths are the other agent's. | `test_an_object_store_failure_does_not_fail_the_save` |
| 13 (jobs) | fixed | `Posting.status_since` (the research date on which a carried posting's status last changed) is part of the archive copy's identity (`jobs.snapshot.identity_bytes(record, status_since)`). A posting that closes and opens again is a new copy and the newest copy says open; a posting that never changed keeps its key. | `test_a_posting_that_returns_to_an_earlier_state_is_a_new_copy`, `test_a_posting_that_closes_and_opens_again_shows_as_open_in_the_archive` |
| 14 | fixed | `Posting.captured_on` is the retrieval day when the posting first became a row, kept on carry (legacy rows: the stored retrieval day). The reader (`PgContentReader._jobs`) prints it as First seen when the feed gave no date. | `test_a_board_only_posting_keeps_the_day_it_was_first_captured`, `test_a_board_only_postings_first_seen_is_the_day_it_was_first_captured` |
| 15 | fixed in part | The gap loop reads only the remainder's stored texts, side by side, and a missing object is skipped (`_remainder_texts`). `gap_pass` keeps the prior claims of untouched postings instead of templating from all texts. Still read once per incremental research: the carried texts (to template carried claims) and the archive rebuild. | `test_a_gap_pass_reads_only_the_remainders_texts`, `test_a_missing_stored_text_does_not_fail_the_gap_pass` |
| 19 | fixed | `previous_delivered_run(..., matching)` compares the stored `lanes_off` and `lexicon_hash` (records before them: `lanes_not_searched` and `config_hash`) with the current ones; any difference means a full pull. `JobsConfig.lexicon_hash` added. | `test_a_research_with_other_matching_inputs_is_not_a_base` |
| 20 | fixed | `lifecycle.create_run` adds the incremental keys (`_incremental_keys`) for the API, the file run's linked research and refresh jobs; a file-only run gets none, a Phase 2 run takes its parent pull's keys. Removed from `replica/jobs.py`. `routes/runs.py` and `file_runs.py` needed no edit. | `test_create_run_adds_the_incremental_keys_for_every_start` |
| 25 | fixed | `save_run` writes the observation of a carried posting at the research date (`_seen_at`). | `test_a_carried_postings_observation_advances_with_the_research_date` |
| 27 | fixed | `SourceBatch.dropped` per call; the active pull does not add to `ConnectorPostingSource.dropped`. | `tests/jobs/test_adapters.py::test_a_lookalike_seen_by_the_window_pull_and_the_active_pull_is_counted_once` |
| 21, 22, 23 | skipped | Owner decisions (English-only cues, the `bain` alias, extra exclusion cues). | none |

Other findings of the review (5, 6, 16 to 18, 24, 26, 28 to 30) were not assigned to this wave.

## Files

Source: `src/jobs/{adapters,claims,config,export,incremental,models,pipeline,ports,repo,snapshot,stage}.py`,
`src/agents/production/{jobs_carry (new),pipelines}.py` (`run_jobs` only),
`src/account_intelligence/connectors/models.py` (`FeedArgs`),
`src/account_intelligence/runs/lifecycle.py` (`create_run`), `src/account_intelligence/replica/jobs.py`,
`src/exports/readers/pg.py` (`_jobs` only), `src/exports/content.py` (`PostingRow.classified_by`),
`config/export_rules.yaml` (jobs Summary paragraphs 1 and 3). `p_signals.py`, `provider.py`,
`providers.yaml`, `posting_rubric.yaml`, `job_lanes.yaml`, `routes/runs.py`, `file_runs.py`,
`readers/jobs.py` needed no edit.

Tests: new `tests/jobs/test_fix_wave_a.py` (11), `tests/jobs/test_fix_wave_a_db.py` (10); changed
`tests/fakes/jobs.py` (`since`, `active_complete`), `tests/jobs/{test_adapters,test_gap_and_resume,
test_pipeline,test_repo_db,test_snapshot}.py`, `tests/connectors/test_contracts.py`.

Generated docs regenerated: `docs/exports/files.md` (`python -m exports.docs`),
`docs/api/openapi.json` (the script rewrites it; it also carries the other agent's route changes).

## Commands and results

All tests as `TEST_DB=ai_test_fixa PGPASSWORD=dev APP_RW_PASSWORD=... MIGRATOR_DATABASE_URL=...
pytest <paths> -m "not eval and not live" -q -p no:cacheprovider`.

* `tests/jobs tests/agents/test_entity.py`: 175 passed.
* Required set `tests/jobs tests/exports tests/agents tests/replica/test_link_archive.py tests/isolation`: 2041 passed, 1 xfailed, 0 failed (17 minutes).
* Earlier wider run (`tests/exports tests/agents tests/replica/test_link_archive.py tests/isolation
  tests/connectors/test_contracts.py tests/runs`): 1937 passed, 3 failed. One was mine
  (`test_entity::test_a_posting_on_a_lookalike_host...` expected `_lookalike` to keep counting: fixed).
  The other two (`test_link_archive::test_the_account_route_serves_the_archived_copy_to_its_tenant_only`,
  `isolation::test_matrix::test_every_discovered_object_is_covered_by_the_matrix`) passed on a
  re-run once the other agent's files settled.
* `ruff check src tests`: all checks passed. `mypy` (699 files): no issues. `lint-imports`: 19 kept,
  0 broken (jobs.repo uses stdlib `logging`, not `obs`). `scripts/gen_loops.py --check`,
  `python -m exports.docs --check`, `scripts/gen_jobs_docs.py --check`: clean.
  `scripts/export_openapi.py` was run once (it ignores `--check`) and wrote the current snapshot.
* The new tests were shown to fail on the pre-fix behaviour for findings 1, 2 and 8 by re-creating
  the old function in a pytest plugin outside the repo (3 of 3 failed as expected); the others fail
  by construction (they call functions or fields that did not exist).

## Deploy notes and what stays open

* Records are forward only: `Posting` forbids unknown fields, so worker or API code from before this
  change cannot load a job record saved after it (`captured_on`, `status_since`, `lexicon_hash`
  and others). Do not roll the worker back below this release after a research has run on it.
* Ledger keys: news and technology requests, and the job feed pull without a bound, hash exactly as
  they did before the flags existed. If an environment ran the commit that added the flags
  (`with_description_only` true), a run in flight across the deploy pays its job feed pull once
  more.
* Old stored records have no `lexicon_hash`: the first research after the deploy compares their
  `config_hash`, and a changed lexicon (Workday, new aliases) means one full pull.
* Phase 1's feed call (`agents/production/wiring.py`, not in this wave) still takes the delta bound
  from the run input. If the pipeline cannot load the previous record at that moment (erased), it
  pulls the whole window under a second key: a rare double pull, never a short record.
* A carried posting classed by the old keyword rules before this release keeps that class
  (carried rows are never reclassified); the reading cost of caps off is unchanged.
* Carried texts are still read once per incremental research (to template carried claims) and once
  for the archive rebuild.
