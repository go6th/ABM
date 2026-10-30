# Fix wave D2 (2026-10-30): pipeline leakage, what was done

Source: `docs/audit/2026-10-30-leakage-review.md` (F1 to F12) and F8 of
`docs/audit/2026-10-30-orchestration-review.md`. Scope of this wave: exports, archive, safety,
workspace tables, documents, erasure, migrations 0093, logging of the Ask route. Nothing is
committed, nothing is deployed, no live provider was called, no real data was deleted (erasure is
code plus tests on synthetic tenants).

Every finding was verified against the code before changing it. None was already guarded, except
one reader that the review listed under F2: `agents/production/people_links.py FactMentions`
already keeps one mention per person name, newest fact first, so it was left alone. Proof
per finding: a test that fails on the old behaviour and passes now (section "Old behaviour"
below shows the run against the old behaviour).

## Per finding

| Finding | Result | Proving test | Main files |
| --- | --- | --- | --- |
| F1 exclusions | fixed | `tests/exports/test_leak_wave_d2.py::test_an_exclusion_added_after_a_run_reaches_every_surface`, `::test_removing_the_exclusion_lets_the_next_build_list_the_person_again`, `::test_an_excluded_company_domain_is_a_forbidden_term_of_the_rebuild`, `::test_a_package_made_before_an_exclusion_is_stale_for_download` | `exports/readers/exclusions.py`, `exports/readers/pg.py`, `exports/builder.py`, `exports/store.py`, `views/materialize.py`, `replica/workspace.py`, `replica/sheets.py`, `replica/file_tabs.py`, `replica/answers.py`, `api/routes/account_workspace.py`, `api/routes/downloads.py`, `api/routes/exports.py`, migration 0093 |
| F2 repeats (open question 13) | fixed | `::test_the_same_fact_recorded_under_two_runs_appears_once_everywhere` | `replica/current.py` (new), `replica/workspace.py`, `replica/sheets.py`, `replica/answers.py`, `api/routes/crm.py`, migration 0093 (indexes) |
| F3 erasure | (a) (b) (c) (d) done; owner decisions listed below | `tests/system/test_erasure_classification.py` (6 tests), `tests/phase3/test_ws1_tenancy.py::test_deletion_dry_run_lists_exactly_what_execution_removes` (now plants and erases all 107 tables), `tests/system/test_erase_allow_list.py` | `config/deletion.yaml`, `tenancy/lifecycle.py`, `tenancy/storage.py`, `tenancy/ports.py`, `api/tenant_plane.py`, `api/routes/operator_admin.py`, migration 0093 |
| F4 contact details in archive copies | fixed | `tests/replica/test_archive_leaks_d2.py::test_a_page_copy_loses_its_contact_details_and_a_news_copy_too` (+ served copy, release text) | `archive_records.py`, `archive.py`, `exports/workspace_files.py` |
| F5 user-triggered build and colleagues' accounts | fixed | `::test_a_users_rebuild_forbids_a_colleagues_private_account` | `exports/readers/pg.py`, `exports/snapshot.py` |
| F6 shared raw object | fixed | `tests/documents/test_raw_files_db.py::test_a_delete_never_removes_the_object_a_colleagues_document_points_at` | `documents/service.py` |
| F7 previous manifest | fixed | `::test_the_change_note_of_a_rebuild_compares_with_the_previous_run` | `exports/store.py` |
| F8 person erasure | small part fixed (web tables); the rest documented | `tests/people/test_archive_erasure_db.py::test_erasing_a_person_discards_the_account_s_materialised_web_tables` | `people/repo.py` |
| F9 `ask.route` log | fixed | `tests/replica/test_ask_route_log.py` | `api/routes/ask.py`, `config/telemetry/logs.yaml` |
| F10 archive addresses | fixed | `tests/replica/test_archive_leaks_d2.py::test_the_archive_stores_and_serves_the_address_without_secrets` (+ `test_strip_secrets_keeps_everything_else`) | `urls.py`, `archive.py`, `archive_records.py` |
| F11 gate host check | fixed | `tests/documents/test_gate_db.py::test_gate_reads_every_member_for_a_provider_host_in_a_file_it_did_not_make` | `documents/gate.py` |
| Orchestration F8 run-level gate failure | fixed | `::test_a_run_level_gate_failure_lists_and_serves_none_of_the_built_files` | `replica/workspace.py`, `documents/gate.py` |
| F12 fixtures naming real accounts | partly: the legal name in one test replaced; the rest is the owner's | none | `tests/exports/test_evidence_register.py`, `replica/answers.py` (a comment) |

## How it works now

### F1: the current exclusions win

* The reader (`PgContentReader.read`) applies the tenant's CURRENT exclusions
  (`tenancy.settings.current`), not the version the run pinned (the pin still carries the
  branding). The contradictory header of `exports/readers/exclusions.py` is rewritten.
* An excluded person's row, findings, registered sources (their profile address, or an address on
  an excluded company's domain), evidence on those addresses and detection addresses are dropped
  in `apply_exclusions`, so the sources list and `build/urls.json` no longer carry them. An
  excluded company stays a forbidden term (the build fails closed on it, as before).
* `exclusion_hash` names the set that was applied (16 hex characters, `none` for no exclusions).
  It is in the snapshot, the build manifest and every stored file's `manifest_json`, and part of
  the set hash (the same bytes under a new list are a new version that says which list it honours).
  `view_materializations.exclusion_hash` records it for the web tables.
* A stored file or a materialised table whose hash is not the tenant's current one is stale: the
  listing (`workspace.deliverables`), the single and zip download, Download everything, the
  delivered-file tabs and the file-shaped sheets do not serve it (`W.stale_kinds`,
  `SH.is_materialised`, `file_tabs._built_under`). The routes that need them build the files
  again first (`refresh_stale_files`: free, no provider; one build per run at a time; a build a
  rule refuses is not retried for ten minutes) and `views.materialize` rematerialises the tables
  (`reset_view_run`, a definer function, replaces the run's derived rows in one transaction).
  Files built before the hash existed count as built with no exclusions, so they rebuild once, and
  only for a tenant that has some.
* Live (pre-delivery) sheets hide rows that name an excluded person or sit on an excluded
  company's domain (`sheets.hidden_ids`, the same text rule as the archive, including the names
  behind an excluded profile address); Ask's rows are filtered the same way.
* A package made before the exclusion changed answers 409 `package_stale` on download
  (`materialize.exclusions_changed_since`).
* Open: a person allowed again reappears in the next build (the research's own pull-time flag is
  untouched). Settings that no longer validate hold the files back instead of failing the page.

### F2: one rule for the account-wide readers

`replica/current.py`: of the rows of one account that are the same thing, only the newest is read
(facts by `fingerprint`, postings by `posting_key`, people by `person_key`, detections by
technology). Used by the account page counts and top five findings, the live sheets, Ask and the
CRM items (which also now serves the newest, not the oldest, ten). The delivered files read one run
and are unchanged. I chose "newest equal row" over "latest run" because uploaded-file facts live in
their own file-mode runs. Consequence: a fact an earlier run found and the latest did not find
again still shows (history), which is the owner's call (leakage review, open question 2).

### F3: erasure

* `config/deletion.yaml` v2: 107 tables erased (children first, checked against the database's
  foreign keys), 12 blocked with a reason (accounts, runs, account_source_counters, sources,
  people tables, users, memberships, llm_calls, the stored objects), 61 kept with a reason
  (ledgers, billing, audit, metering, security history, tenant configuration, platform
  evaluation). `never` is now a mapping table to reason.
* `tests/system/test_erasure_classification.py` fails when a table that carries a `tenant_id` or a
  tenant `thread_id` is in none or in two of the lists, when a name is stale, a reason is missing,
  the erase order would break a foreign key, or the database does not allow what the plan lists.
* Migration 0093 grants the eraser DELETE on exactly the plan's tables and widens `erase_rows`'s
  allow-list to them (same definer pattern as 0091/0092, `-- reviewed:` comment, down file).
* Objects: `tenancy.lifecycle.erase_objects` deletes every object of the tenant by prefix
  (`tenancy.storage.object_prefixes`: its tree, the delivered files `<tenant>/...`, the upload and
  quarantine prefixes) after the rows, through the new `ObjectStore.delete_prefix` (GCS adapter
  refuses any prefix that is not exactly one tenant tree). The existing operator execute call
  passes the plane's object store; without a configured store the item stays blocked. No new
  endpoint or trigger.

## Owner decisions needed

1. F1 default: the current exclusions win and a person allowed again is listed again. The alternative
   (union with the pinned list) would keep an old exclusion alive after the admin removed it.
2. F1: stale files rebuild on view (free). If the owner prefers a manual "Rebuild" button, remove the
   call in `refresh_stale_files`; the files then stay held back until `POST /v1/exports`.
3. F2: "newest equal row" keeps facts the latest run did not find again. Superseding them at the end
   of a run (migration-free but careful writer) changes what history means for the Library and the
   alerts.
4. F3 blocked items (`accounts`, `runs`, `users`, `memberships`, `llm_calls`, `sources`, `people*`):
   pseudonymise, keep, or delete with the ledgers. Until decided a tenant deletion ends `blocked`,
   never `completed` (as before). Secret Manager versions behind erased connection rows are not
   destroyed (`SecretStore.destroy` is unconfigured); the Redis cache entries keyed by tenant are
   not purged; the stored objects are deleted only in the cloud (the local dev store has no
   adapter). Counsel should confirm that `tool_calls`, `audit_events` and the money ledgers stay.
5. F8: nulling facts and evidence that quote an erased person, and the delivered files and workspace
   zips that hold them, needs counsel (D-06). Done here: the account's web tables are discarded.
6. F12: tracked fixtures that name real accounts. Left alone by this wave; **done afterwards on the
   owner's decision of 2026-10-30** (`docs/audit/2026-10-30-owner-decisions-done.md`, change 4): the
   real-account workspace zip, its tabular tests and eval cases are parked in
   `unwanted-things/real-account-fixtures/` and replaced by a synthetic workspace; every other
   mention in code, config, tests, docs and `config/skill/archive/` is neutral wording.

## Old behaviour (the new tests fail on it)

The working tree is shared with other agents and uncommitted, so the old code could not be
checked out. Instead a copy of the final tree was turned back into the old behaviour for each
finding (`mkold.py` in the scratchpad: the reader applies only the pinned exclusions, the files
and tables are never stale, `NEWEST_*` is empty, `previous_manifest` ignores the run, the other
accounts are listed in the caller's scope, the document delete counts in the caller's scope,
`redact_contact` and `strip_secrets` return their input, the gate reads visible text only, a
run-level verdict holds nothing back, the erase loop in `people/repo.py` is gone, the log carries
the cells, `deletion.yaml` is `git show HEAD:config/deletion.yaml`) and the new tests were run on
it with their own scratch database:

* 23 failed, 7 passed. The 7 that pass test building blocks that have no old counterpart or that
  the copy does not revert: `object_prefixes`, `delete_prefix` batching, the grants of the eraser,
  the object-deleting execute pass (the old code had no adapter and no caller: `tenant_prefixes()`
  was never called), the log allow-list file, one non-secret URL, and the existing raw-file test.
* The failing ones, by finding: F1 (4 tests, e.g. the person is still in the rebuilt People list,
  the web table and the live sheet), F2 (1), F3 (2: 104 tables unclassified, order), F4 (3), F5 (1:
  the colleague's account is missing from the forbidden terms), F6 (1: the shared object is
  deleted), F7 (2), F8 (1), F9 (1), F10 (5), F11 (1), orchestration F8 (1).
* On the real tree: all of them pass.

## Commands and results

Scratch databases of my own (`ai_test_d2a` to `ai_test_d2g`), `-m "not eval and not live"`, never
sourcing `.env`, one pytest session per database, in parallel:

| Session | Directories | Result |
| --- | --- | --- |
| A | `tests/exports` | 650 passed, 1 failed: `test_db.py::test_repeat_build_writes_nothing_and_redelivery_supersedes` expected the old change note (F7); the test was changed to the new rule; rerun passed |
| B | `tests/replica` | 311 passed |
| C | `tests/people tests/jobs tests/grading tests/guardrails tests/system tests/unit` | 1168 passed, 2 failed: `test_static_guards::test_one_retry_owner_per_failure_class_w6` (docs/architecture/loops.md was stale; regenerated with `scripts/gen_loops.py`, passes) and `test_single_write_paths::test_each_table_is_written_only_by_its_owner` (`research_jobs` is written by `runs/recovery.py`, a D1 file: D1 must add it to `OWNERS`) |
| D | `tests/documents tests/phase3 tests/isolation` | 1272 passed, 1 skipped, 1 failed: `test_ws4_views::test_exclusions_at_render_time...` expected `apply_exclusions` to return the same object for no exclusions; fixed in the code, rerun passed |
| E | `tests/api tests/crosscut tests/db tests/obs tests/phase4 tests/research_files tests/tracker tests/spend` | 1096 passed |
| F | `tests/agents tests/integration tests/e2e tests/fuzz tests/runs tests/worker tests/harness tests/connectors` | 723 passed, 1 xfailed, 1 failed: `connectors/test_router_product.py::test_no_vendor_name_in_keys_logs_errors_or_results` fails only after the agents tests in one session (the `httpx` INFO line names the provider host) and passes alone (18 passed); not related to this wave |
| after the fixes | the 13 files that touch exclusions, the gate, raw files, the erase list, the static guards | 116 passed; the four new modules and the changed old tests 61 passed |

Static checks, all clean: `ruff check src tests`, `ruff format --check src tests`, `mypy` (703
files), `lint-imports` (19 contracts kept), `scripts/gen_loops.py --check`,
`python -m exports.docs --check`, `scripts/gen_jobs_docs.py --check`,
`scripts/export_openapi.py --check`, `scripts/gen_web_client.py --check`,
`scripts/check_route_catalog.py`, `scripts/wiring_audit.py --check`,
`scripts/check_telemetry.py`, `scripts/check_guarantees.py` (510 tags, 0 dangling), the migration
lint (`OK: 182 file(s)`), `scripts/check_rls_catalog.sql` on a migrated database (no violations).
Migration 0093: `alembic upgrade head`, `downgrade 0092`, `upgrade head` round trip on a fresh
database. `alembic check` reports model drift in `accounts`, `runs`, `sources` and
`account_source_counters` (owner columns from migration 0063 that the ORM models do not declare);
it is unrelated to this wave. The web was not changed, so `tsc`, `eslint` and `vitest` were not
run.

`EXPECTED_REVISION` in `api/routes/health.py` is `0093` (the highest migration present; re-read
at the end). D1 may add 0094 and must bump it.

## Things D1 needs to know

* `tests/system/test_single_write_paths.py` fails on `research_jobs` written by
  `runs/recovery.py` (add the file to `OWNERS["research_jobs"]`).
* `docs/architecture/loops.md` was regenerated once with both our loops in it; regenerate again
  after changing a `LOOP(...)` comment.
* `documents/gate.py` gained `run_level_failed`; `replica/workspace.deliverables` now also holds
  back every file of a run whose latest verdict is a run-level FAIL. The rebuild path
  (`POST /v1/exports`, the stale-file rebuild) does not run the gate and inherits the run's last
  verdict, as the orchestration review noted.
