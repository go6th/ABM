# Fix wave B: security review findings (archive, people, naming, converter), 2026-10-29

Source: `docs/audit/2026-10-29-review-security.md` (F1 to F21) and the people and archive findings of
`docs/audit/2026-10-29-review-pipeline.md` (5, 6, 12, 13, 16, 17, 22, 24, 26). Nothing committed, no research
run started, no file deleted. The jobs pipeline files belong to the other agent and were not touched.

## Decisions recorded here

* **Profile copies are internal.** A `link_archive` row of kind `profile` holds a person's name, title, headline
  and location. It is an input of the profile matcher (`archive.profile_links`) and nothing else. No client
  route lists or serves kind `profile` (`archive.CLIENT_KINDS`), and no table row offers an archived-copy link to
  one. Reason: the People List omits excluded, out-of-scope, review and merged people on purpose; a copy of them
  that a viewer could open defeats that. The People List "Archived copy" link for a listed person therefore no
  longer exists (the live profile link stays).
* **Only listed people are written.** `PgPeopleRepo._archive_entries` writes `in_scope`, not merged, not
  internal-only people with a profile link.
* **No object delete exists** in the platform payload store (`platform.ports.PayloadStore` has `put` and `get`).
  Erasure therefore deletes the INDEX rows; a body whose index row is gone cannot be reached by any route, and
  profile bodies are never served anyway. The bodies stay in the store until a delete adapter exists
  (`config/deletion.yaml` `raw_payload_objects` and the 0091 header now say so).
* **Pipeline 17 skipped** (owner decision, as instructed).

## Per finding

| # | Result | What changed | Regression tests (fail on the old code) |
| --- | --- | --- | --- |
| F1 | fixed | `people/repo.py` writes listed people only; `payloads.py` marks any profile-host page that reads as a person's page as kind `profile` (internal); `account_workspace.py` `/archive` and `/archive/list` use `CLIENT_KINDS` and the tenant exclusion filter (`exports/readers/exclusions.py: text_excluded`) on title, address, text and record fields; `attach_archives` skips profile copies; web filter "Profiles" removed. Purge job for existing rows (point 3) not built: such rows are no longer served by any route and go with a person's erasure. | `tests/replica/test_archive_guard.py`: `test_a_profile_copy_is_never_listed_or_served_and_never_linked`, `test_the_tenant_exclusions_hold_on_the_archive_routes`, `test_an_excluded_profile_address_hides_the_pages_that_name_it`; `tests/people/test_repo_db.py::test_round_trip_and_reentry_makes_no_paid_call` (listed people only) |
| F2 / pipeline 6 | fixed | migration `0092_archive_erasure_meta`: reviewed `SECURITY DEFINER erase_archive_urls(p_account, p_hashes, p_name)` (owner `tenant_eraser`, tenant from `app_tenant()` only, refuses unless the account holds an erased person, REVOKE PUBLIC, EXECUTE app_rw); `PgPeopleRepo.erase` calls it for the person's addresses and name; `ArchiveResolver` skips erased people (canonical profile URL of tombstoned `people` and `person_profiles`). `deletion.yaml` unchanged in its list (the new function is not `erase_rows`; the allow-list test still ties). Isolation matrix lists the function. | `tests/people/test_archive_erasure_db.py`: `test_erasing_a_person_deletes_their_archive_rows_and_no_one_elses`, `test_the_erase_function_is_narrow`, `test_a_second_users_rows_are_erased_too`, `test_the_resolver_never_relists_an_erased_person_and_wants_the_account_in_the_title` |
| F3 | fixed | `agents/naming.py`: the vendor word is a hit in every case and position; exempt only lowercase "in parallel" and "parallel processing/execution/workflows/computing/tasks/teams/lines/universe/to/with". NFKC and zero-width folding before matching. | `tests/agents/test_naming_words.py` (24 vendor positions incl. all audit cases, 8 English phrases); `tests/replica/test_file_tabs.py::test_ordinary_english_is_not_a_leak_in_the_gate_and_the_page` |
| F11 | fixed | `_vendor_hits` is one linear pass, looks 8 characters back and 24 forward, stops at 1,000 hits | `test_a_hostile_page_of_vendor_names_is_scanned_in_linear_time` (3.5 MB) |
| F4 / F16 | fixed (see skipped parts) | `ExportBuilder.build_all` runs `assemble` in `asyncio.to_thread`; `LibreOffice` runs under `/bin/sh -c 'ulimit -v; ulimit -t; exec ...'` (6 GiB address space, 240 s CPU), `start_new_session=True`, the whole process group is killed on timeout and afterwards, at most 2 conversions at once, a timeout is not re-run, `last_failure` explains `not_run` | `tests/exports/test_render_check_runtime.py` (loop stays responsive during a slow converter; whole group killed; limits and own session) |
| F5 | fixed | `attach_archives` offers no copy for a provider-domain address; `scrub_rows` also drops an archive address that names a provider | `test_the_archive_address_of_a_provider_link_is_dropped_with_the_link` |
| F6 | fixed | archive routes, page, cell and tab pass the other accounts' names and domains and the excluded companies as forbidden terms (`restricted_terms`, as the exports); a refused field is blanked and named under `withheld` | `test_a_copy_that_names_another_account_of_the_client_is_withheld_per_field` |
| F7 / pipeline 16 | fixed | `candidates_for` keeps only pages whose company part names the account's brand; `resolve_unlinked` accepts a candidate only when it carries an employer (and the same one as the record) | `tests/people/test_archive_links.py` (namesake, no-company title, erased page); corpus resolver record now carries its employer |
| F8 | fixed | `0089_people_fields.down.sql` maps `senior_director`/`associate_director` to `director` and `other` to NULL (old check allows NULL; the full level stays in `classification_json`), then adds the check validated | `test_the_0089_downgrade_maps_the_wider_levels_back_and_leaves_erasure_working` (rolled-back transaction) |
| F9 | fixed (my part) | one default write guard: `agents.naming` registers `archive_guard` as `archive_records.default_guard` (so `PgPostingRepo`/`core.py` use it without a change there); a legacy plain-text copy is served through `redact_contact`; people repo takes `guard`, wired to `archive_guard` | `test_a_legacy_text_copy_is_served_without_email_or_phone` |
| F10 | fixed | new `exports/urlcheck.py` (`is_safe_http_url`: http/https only, host, no credentials, no whitespace anywhere incl. trailing newline, no control/zero-width/bidi, no backslash, 2,048 max) used by `models.Url`, `word.contract.Href`, `Book.link`, `Book.body` (raises `SchemaViolation`) | `tests/exports/test_url_sinks.py` (46 cases) |
| F12 | fixed | migration adds `link_archive.meta` (index fields, filled by `entry_for_record`, people and detection writers); `list_items` reads no body for a row that has it (old rows: at most 50 bodies per page); one query for page and count; JSON view cuts text at 100,000 characters (`truncated`), the download is whole; offset limit 100,000; per-user bucket `archive_read` (120 at once, 2 a second) | `test_the_index_reads_the_fields_beside_the_row_not_the_bodies`, `test_the_copy_answers_a_capped_text_and_the_download_gives_all_of_it`, `test_the_archive_reads_have_a_per_user_limit`; `tests/api/test_user_rate_limits.py` (both routes take the token) |
| F13 | fixed (table side) | insert is `ON CONFLICT DO UPDATE SET archived_at` (only forward); column grant plus trigger `link_archive_touch_only` (no other column, never backwards). 0088 down comment corrected (`rule` is the true closest label). The up migrations 0088 and 0089 are applied: their lock behaviour cannot be changed now. Posting identity bytes stay with the other agent. | `test_a_page_that_returns_to_earlier_content_is_the_newest_copy_again`, `test_only_archived_at_may_change_and_only_forward` |
| F14 | fixed | `tech_stack_items` insert names the account's owner | `test_the_rows_name_the_accounts_owner_whoever_writes_them` |
| F15 | fixed | JSON download audited `export.download_archive`; the cell route drops a provider `href` (no 422) | `test_the_copy_answers_a_capped_text_and_the_download_gives_all_of_it` (audit row) |
| F17 | skipped | `.claude/launch.json` is the owner's dev file, not in my list | |
| F18 | skipped | needs a dependency change (`defusedxml`) in `pyproject.toml`, outside my files; mitigated as the review says | |
| F19, F20 | no action | info only | |
| F21 | done | tests listed above: exclusion vs archive, two users of one client (`test_another_user_of_the_same_client_cannot_reach_the_archive_of_a_private_account`), erasure vs archive, render check off the loop, naming positions | |
| pipeline 5 | fixed | same as F1 | |
| pipeline 12 | fixed (people, Phase 1, detections) | `archive.write_failed` logged and the work goes on: people repo (`on_archive_failure`, the package may not import `obs`; the application passes a logger), `StorePayloads.put`, `_insert_detections`; DB inserts run in a savepoint | `test_an_archive_failure_never_fails_the_research` (store down, insert failing). Jobs (`jobs/repo.py`) already carries its own try/except from the other agent. |
| pipeline 22 | fixed | alias `bain` removed (`bain (&\|and) co(mpany)?` remains); frozen test config and `docs/people/rubric.md` regenerated | `tests/people/test_si_firms.py` |
| pipeline 24 | fixed | `write_stack` uses the first matched record that has an id (as Phase 1) and counts skipped empty items (`stack_skipped_empty`) | `tests/exports/test_tech_stack_db.py` |
| pipeline 26 | fixed | person-name search results are archived only while the tenant's people data consent is valid (`StorePayloads._archivable`); deny by default for those rows | `tests/replica/test_archive_guard.py::test_a_person_name_search_result_is_not_archived_without_people_consent` |
| pipeline 13 | fixed (table side) | see F13 | |

## Parts not done, and things the other agent or the owner must do

1. `src/jobs/repo.py` builds `ArchiveEntry` for postings without `meta=AR.index_meta(snap)`. Until it does, posting rows have no index fields beside the row and the index page reads their bodies (50 per page at most). One line in `jobs/repo.py` (both `ArchiveEntry(...)` calls for structured records).
2. `src/exports/readers/pg.py` calls `LA.lookup(...)` for the archived copy behind each source link without `kinds=LA.CLIENT_KINDS`; a source link that is a profile page would carry an archive address that now answers 404. Pass `kinds=LA.CLIENT_KINDS`.
3. `src/account_intelligence/replica/table_export.py::_link_cell` writes a hyperlink without `is_safe_http_url` (not my file).
4. `tests/agents/test_entity.py::test_a_posting_on_a_lookalike_host_is_dropped_before_it_is_stored` fails (`jobs.adapters` counter `dropped`, pipeline finding 27): the other agent's area.
5. F16 not done: `libreoffice-calc` is still unpinned in `docker/*.Dockerfile` (DL3008 like the rest) and no `-env:` network or macro option was added; `infra/stack/main.tf` is unchanged (the render checks stay on, now off the event loop).
6. The People List "Archived copy" link for a listed person is gone by decision (above). Name-based deletion on erasure also deletes the profile copy of a namesake (same name in a title): erring toward removal.
7. A page of some third party that mentions an erased person stays in the archive (public page text); once the person is on the exclusion list the routes withhold it.

## Files

Source: `src/account_intelligence/archive.py`, `archive_records.py`, `api/contract.py`, `api/routes/account_workspace.py`, `replica/sheets.py`, `replica/file_tabs.py`, `src/agents/naming.py`, `agents/production/payloads.py`, `people_links.py`, `pipelines.py` (`run_people` wiring, `write_stack`, `_insert_stack`, `_insert_detections`), `src/people/repo.py`, `archive_links.py`, `mentions.py`, `src/exports/builder.py`, `verify.py`, `urlcheck.py` (new), `models.py`, `excel/common.py`, `word/contract.py`, `readers/exclusions.py`, `src/account_intelligence/api/routes/health.py` (`EXPECTED_REVISION = "0092"`).
Config: `config/people_rubric.yaml`, `config/deletion.yaml`, `config/rate_limits.yaml`.
Migrations: `0092_archive_erasure_meta` (`.sql`, `.down.sql`, `versions/0092_archive_erasure_meta.py`, down_revision 0091; 0092 did not exist when I finished), edits to `0088_posting_heuristic.down.sql`, `0089_people_fields.down.sql`, header comment of `0091_erase_new_tables.sql`, `migrations/README.md`.
Generated: `docs/api/openapi.json`, `web/lib/gen/api.gen.ts`, `docs/WIRING.md`, `docs/architecture/loops.md`, `docs/people/rubric.md`.
Web: `web/components/accounts/ArchiveView.tsx`, `web/app/(client)/accounts/[domain]/archive/page.tsx`.
Tests (new): `tests/replica/test_archive_guard.py`, `tests/people/test_archive_erasure_db.py`, `tests/exports/test_render_check_runtime.py`, `tests/exports/test_url_sinks.py`. Tests (changed): `tests/agents/test_naming_words.py`, `tests/replica/test_link_archive.py`, `tests/replica/test_file_tabs.py`, `tests/people/test_archive_links.py`, `test_repo_db.py`, `test_si_firms.py`, `tests/exports/test_tech_stack_db.py`, `tests/api/test_user_rate_limits.py`, `tests/isolation/matrix/matrix.yaml`, `tests/fixtures/synthetic/people/corpus.yaml`, `tests/fixtures/synthetic/jobs/config/people_rubric.yaml`.

## Commands and results (scratch database `ai_test_fixb`, `-m "not eval and not live"`)

* `pytest tests/replica tests/people tests/agents tests/exports tests/phase3 tests/api` plus the static-guard and allow-list tests: 2034 passed, 1 xfailed, 2 failed. One failure was the old "Parallel processing" expectation (test fixed: 26 passed on re-run); the other is item 4 above (other agent).
* `pytest tests/isolation tests/system`: 970 passed, 1 failed (`test_one_retry_owner_per_failure_class_w6`: the loops document was stale; regenerated, 9 passed on re-run).
* New and changed tests re-run after the last edits (archive guard, erasure, link archive, file tabs, allow-list): all passed.
* `cd web && npx vitest run`: 273 passed (one earlier run showed 1 failure that did not repeat; the second run was clean). `npx tsc --noEmit`: clean.
* `ruff check src tests`: clean. `mypy`: no issues in 699 files. `lint-imports`: 19 kept, 0 broken (the people package may not import `obs`: fixed by the `on_archive_failure` hook).
* `gen_loops.py --check`, `export_openapi.py --check`, `gen_web_client.py --check`, `check_route_catalog.py`, `wiring_audit.py --check`: all OK after regenerating.
* `lint_migrations.py migrations`: OK, 180 files. RLS catalogue check on the migrated scratch database (`.claude/skills/.../check_rls_catalog.sql`, run through psycopg): no violations.
