# Fix wave C: exports, web and end-to-end findings (2026-10-29 and 2026-10-30)

Sources: `docs/audit/2026-10-29-review-exports-web.md` (29 findings), `docs/audit/2026-10-29-e2e-report.md`
(D-1 to D-15), the wave A and B leftovers. Nothing was committed, no paid run was started (the one live
reproduction used the fake model and fake providers on the scratch database `ai_e2e_c`, with the servers
stopped afterwards), no file was deleted. All test data is synthetic (`.example`); the reference account's
name appears in no code, config or test (nine comments and two config strings that did, finding 28, were
reworded).

No migration was added: the head stays 0092, `EXPECTED_REVISION` and `migrations/README.md` are unchanged.

## Decisions recorded here

* **Every exported posting is a registered source.** The job repository registers the link of each posting
  the Job Postings file lists (`jobs.export.is_exported`) through the one writer of source codes
  (`get_or_create_source`) when it saves a research (`jobs/repo.py register_posting_sources`). A rebuild of
  the files of a run saved before this release registers the links it is missing (only the missing ones:
  a source's last-seen reference is not moved back) before the snapshot is taken (`PgContentReader`, wired
  with `tenant_txn` in `exports/wiring.py`). Sources are never deleted; codes of existing sources do not
  change. The Evidence file lists a source only when a file cites it, so a registered posting link appears
  there as the posting's own row (Part B), as before for the cited ones.
* **Cached formula values by direct OOXML insertion, not by a LibreOffice re-save.** The plan functions
  (`plan_jobs`, `plan_people`, `plan_tech` and the Counts lines) already give every formula's value; the
  builder now writes it beside the formula (`<v>`, and `t="str"` for text) in the one package it already
  normalises (`exports/ooxml.py cache_formula_values`). Reasons: a LibreOffice round trip rewrites the
  stylesheet, theme, named styles, conditional formats, hyperlinks and widths, so "styles byte-stable" and
  the strict comparison the brief asked for could not be met without re-implementing the comparison; the
  direct step touches only the formula cells, keeps every other part byte for byte, is deterministic (no
  engine, no clock, same zip settings) and works where LibreOffice is not installed. `fullCalcOnLoad` stays
  on, so Excel and LibreOffice still calculate on open. Two checks guard it: `workbook_cached_values`
  (every planned cell's stored value equals the value computed in code) runs on every build, and
  `recalc_check` now strips the stored values before LibreOffice sees the file, because LibreOffice keeps a
  stored result as it is (shown: a stored 999 came back as 999), which would have made the recalculation
  check read the file back instead of calculating it.
* **One rule for an unknown posting status: it is Open.** `labels.posting_status.unknown` prints `Open`
  (config comment explains), so the Jobs column, the Summary matrix (Total = Closed + Open), the Counts
  sheet, the Platform Ranking's "Open now" (already `status != closed`), RESTORE.md and the Word tables
  agree. The stored value stays `unknown`. The website's status chips are one per printed word (no second
  "Open" chip).
* **No dash as punctuation in a title.** `MapContext.titled` (posting and person titles): a spaced dash or
  hyphen becomes a comma, a lone en or em dash a hyphen, the same two steps the website's table download
  uses. Only the delivered copy changes. The people "Title as reported" is treated like a posting title (the
  reference holds no dash anywhere).
* **People workbook: no "Archived link" column, confirmed.** A profile copy is internal (wave B decision:
  it holds a person's name, title, headline and location and no client route serves it), so the People List
  has no archived-copy link and no new-row flag; the extra Ref and Source columns sit after the 13
  reference columns. Nothing added.
* **Dead governor waiters starved a resumed run (D-4).** Cause found and fixed in configuration, see item 11.

## Per item

| # | Result | What changed | Tests (each fails on the old code unless noted) |
|---|--------|--------------|-------|
| 1 | fixed | Registration above. `readers/jobs.py` refs follow the listed rows (`J-0001`, `J-0002`, ...; People `P-`, Tech `D-` the same). The Counts line reads "Postings not listed (no usable posting link)" and counts a posting with no usable address, which the manifest also lists as `jobs:<key>:missing_url` or `bad_url`. | `tests/exports/test_db.py`: `test_every_listed_posting_gets_a_source_code_though_no_fact_cites_it` (no hand registration; every exported posting has a source; codes stable on a second build), `test_the_counts_say_no_posting_was_left_out` (Counts line 0, refs contiguous), `test_a_run_saved_before_the_links_were_registered_is_repaired_on_rebuild` (old behaviour kept for a reader without the registering transaction; repaired with it); `test_fix_wave_c.py::test_refs_never_skip_a_number_when_a_row_is_withheld`; the old test `test_postings_without_registry_codes_are_withheld` was rewritten (it encoded the old behaviour). |
| 2 | fixed | `exports/contenturls.py clean_urls`, applied once in `MapContext.__post_init__` (so the export mappers and the website tables, which share `map_snapshot`, both get it), with `account_intelligence/urls.py clean_http_url`: strips surrounding whitespace, encodes inner spaces, lower-cases scheme and host; refuses (None) anything else (javascript, ftp, file, data, credentials, no host, control, zero-width and bidi characters, backslash, over 2,048). A posting, person or detection with no usable address is withheld with the reason `bad_url`; a finding or an archived-copy address is left out and listed; a registered source that cannot be repaired is dropped from the registry view (its citations then fail loudly: corrupt provenance is not hidden). | `test_fix_wave_c.py::test_a_bad_posting_address_is_repaired_or_the_row_withheld` (17 variants: space, upper-case scheme, padded, trailing newline, javascript, ftp, file, data, newline, tab, credentials, no host, scheme-less, backslash, bidi, zero-width, 2,100 characters), `test_every_other_address_field_survives_a_bad_value` (sources, both people links, findings, detections, archive addresses), `test_the_address_cleaner_repairs_spelling_and_refuses_the_rest`. With the cleaner switched off, 18 of the 20 tests of the file at that point failed (the two that did not are the cleaner's own unit test and the refs test, which fails on the old reference numbering by itself). |
| 3 | fixed | Cached values, decision above. `Book` records each formula's planned value, `Book.bytes` calls `cache_formula_values`; new check `workbook_cached_values`; `recalc_check` strips first. | `test_every_formula_cell_shows_its_value_without_a_spreadsheet_engine` (openpyxl `data_only` and pandas read every Summary, Counts and Ranking formula cell, 40+ cells across the three books), `test_stored_results_are_removed_again_before_a_spreadsheet_engine_calculates` (real LibreOffice). |
| 4 | fixed | `SheetView.tsx`: rows are keyed by the record (`rowKeys`: a hash of the row's cells, links and link, with a counter for rows that print the same), not by `n-index`. | vitest `an expanded cell stays with its own posting when the table is sorted again` (fails with the old key, shown), `two rows that print the same still get their own keys`. |
| 5 | fixed | See numbers below. Hyperlink relationships in constant time (`render_cli._LinkIds`, same ids as `relate_to`, byte-identical); a Word table of 400 rows or more is written as text and spliced into the saved document instead of an element tree (`render_cli._table_rows_streamed`, canonical-XML identical); the leak scan skips a pattern that provably cannot match (`safety/prefilter.py`: literal needles read from the parsed pattern, ASCII folding checked against the engine's case tables for every code point, guarded names found by their words in one pass) and scans a large text in slices; verification parses Word XML as a stream (`verify._stream`); the dash scan blanks exempt passages only on the lines that hold a dash (`verify.dash_count`, equal to the plain rule on 400 random cases); the workspace zip's third-party scan skips the five files already scanned. LibreOffice's allowance is 120 s plus 4 s per unpacked megabyte (cap 900 s; the 7,000 posting Jobs workbook, 58 MB unpacked, gets 352 s and needs about 106 s), its CPU limit scales with it, and a check that did not run is listed in the manifest (`checks`: `not_run: the spreadsheet conversion did not finish (timeout after N s)`) and logged as `exports.check_not_run`. | `tests/exports/test_scale.py::test_seven_thousand_postings_build_in_bounded_time_and_memory` (child process, time and `VmHWM`), `test_fix_wave_c.py` (`test_the_linear_link_ids_are_the_ids_python_docx_would_give`, `test_a_table_written_as_text_is_the_table_written_as_elements` x2, `test_the_fast_dash_count_is_the_plain_dash_count`, `test_many_passages_over_a_large_text_do_not_multiply`, `test_the_converter_time_grows_with_the_package_it_converts`, `test_a_check_that_did_not_run_is_listed_in_the_manifest_with_its_reason`), `tests/unit/test_prefilter.py` (21: equality with the plain scan on adversarial and random texts, slices of 1 to 33 characters, 3,000 guarded names, every dictionary platform, the folding for every code point up to U+2FFFF). |
| 6 | fixed | Decisions above. | `test_an_unknown_status_prints_open_and_counts_as_open_everywhere`, `test_a_posting_title_carries_no_dash_as_punctuation`; three existing tests whose expectations encoded the old rule were updated on purpose (`test_evidence_part_c` x2, `test_evidence_parts`, `test_lying::test_a_dash_in_a_stored_title_is_not_punctuation`). |
| 7 | fixed (People and Tech); decisions noted | A cell over Excel's limit in a workbook without a Long text sheet is cut and closed with a note, counted in the manifest's `long_text_parts`; Excel's length is counted in UTF-16 units (an emoji is two; also fixes review finding 18) for the Long text split as well; stack and detection names and titles are cut at 1,000 characters in the model (they are also row keys and Word cells, whose limit is 40,000: a 40,000 character title no longer stops the five files). People "Archived link": none, decision above. Extra columns are after the reference columns. | `test_an_over_long_cell_in_people_or_tech_is_cut_and_counted`, `test_excel_counts_utf16_units_so_a_description_of_emoji_splits_in_time`. |
| 8 | fixed | `data/releases/<day>_<title words>-<hash of the address>.md`, one per archived page, news item and posting (client kinds, the newest structured record per address else the newest page copy, newest capture first, at most 2,000 files of 60,000 characters: `workspace.releases` in `export_rules.yaml`; `max_members` 2,100), front matter `title:`, `url:`, `date:`, `source: 6th agents`, a blank line, the text, as in the reference zip (`.md`). Built by the reader from the link archive (`PgContentReader._releases`, no paid call), through the exclusion filter (`apply_exclusions`), the tool-name mask and the leak scan; a copy a rule refuses is left out and listed by a hash of its address (`workspace.releases:<hash>:leak` or `third_party`; neutral rule names, because the manifest is scanned like every member). RESTORE.md lists `releases/ (N text files ...)` and step 2 now reads "To rebuild the deliverables from this run, ask your 6th agents contact" (neutral words; it mentions that the archived copy of a file opens after sign in). | `test_fix_wave_c.py`: layout and front matter, determinism and stable names (same bytes in another order), refused copies, the cap; `tests/exports/test_db.py::test_the_workspace_zip_carries_the_accounts_archived_copies` (real archive: news page and read postings in, a profile copy never, a rebuild reuses the stored set). |
| 9 | fixed | ZodError and non-JSON bodies become `ServerApiError(502)` (`web/lib/server-api.ts`), so every page shows "could not be loaded"; numbers in `SheetView` use one locale (`Intl.NumberFormat("en-GB")`); the button is labelled honestly "Download table as Excel" (the delivered file keeps its own "Download the file" button in the page head); titles and names over 40 characters wrap (`td.wrap`, max width 520 px, `overflow-wrap`); the archive index's first cell links to the copy (visible at 390 px); the archive "All" view and the archived link prefer the structured record (`posting`, `feed_item`, `detection`) over a `page` copy of the same address, in `list_latest` and `lookup` (and `lookup` filters kinds in SQL: before, the newest row decided and a profile copy hid an older page copy); the stored-table path now colours a new-posting row (`new_row` on the whole row) and a person's Priority cell like the file (`sheets.row_fills`). | vitest (`server-api` 502, `sheet-view` x3, `archive-pages` first column), `tests/replica/test_link_archive.py::test_the_structured_record_of_an_address_wins_over_a_newer_page_copy`, `tests/replica/test_fix_wave_c.py` (fills x2). |
| 10 | fixed | `jobs/repo.py` passes `meta=AR.index_meta(snap)`; the reader passes `kinds=LA.CLIENT_KINDS` to both `lookup` calls; `table_export._link_cell` uses `is_safe_http_url` and "Download table as Excel" cuts cells at Excel's limit (`fit_cell`); `test_entity::test_a_posting_on_a_lookalike_host_is_dropped_before_it_is_stored` already passes (the per-call `dropped` semantics of wave A finding 27 are right: the count is per call, one `_lookalike` call counts one; nothing to change). | `tests/jobs/test_archive_snapshot_db.py::test_a_posting_copy_stores_its_index_fields_beside_the_row`, `tests/replica/test_fix_wave_c.py` (`_link_cell`, `fit_cell`). |
| 11 | fixed (resume stall); documented (budgets) | **D-4.** Reproduced on `ai_e2e_c` (fake model and providers; stop at 28 s; resume): the resumed run adopted all 101 finished calls of the stopped run, then three slices of about 33 s ended `no_progress`. `provider_waiters` held only the resumed run's own queued units, yet no slot was granted although no lease was held: the governor gives the next slot to the **oldest live waiter**; the stopped run's queued units (and, in every later slice, the previous slice's units) are dead, never poll again, stay in the table for `waiter_ttl_s` (60 s), rank ahead by age and never take the slot, so nobody is granted anything and each slice's 30 s wait ends "no slot". `config/parallel.yaml waiter_ttl_s` 60 to 10 (twenty polls). The same reproduction then finished in 30 s (99 adopted, 24 new). **D-5** (a repeat or resumed run reserves a tiny budget from earlier cheap runs with the caps on): documented in `docs/guide/06-known-problems.md` (16), not changed (the owner chooses the floor). Both are entries 15 and 16 of the guide's table. | `tests/parallel/test_governor.py::test_a_dead_runs_queued_units_do_not_starve_the_next_run` (starved at 60 s, not at the configured value, same decision rule as the SQL function). |

## Numbers for item 5 (7,000 postings with 2,000 to 12,000 character descriptions, all read in full, 800 people)

Same synthetic account and script before and after (`ExportBuilder.build_all` with fake ports, the in-process
Word renderer, peak resident memory of the process):

| | before | after |
|---|---|---|
| Build time, no converter | 219 s | **65 s** (110 s when the machine ran 1.6 times slower; a micro benchmark showed it) |
| Peak memory | 1,726 MiB | **692 MiB** (VmHWM 676 MiB in the test's child process) |
| Same with LibreOffice recalculation of the three workbooks | did not finish inside the 120 s allowance (the review: 133 s needed) | 171 s total, recalculation about 106 s, allowance 352 s, check `passed` |

Where the time went: python-docx `relate_to` (quadratic), the leak scan (a regular expression per pattern
per cell and per XML member), a thousand `replace` calls over a 100 MB text in the dash rule, the platform
matching of 31 platforms over every posting, scanning the five files a second time inside the zip. Memory:
the Evidence Word table (29,625 rows) as an element tree (about 800 MiB at 7,000 postings), whole-tree XML
parses in the verification (about 150 MiB each) and a list of every word of a 140 MB document (1.2 GiB).
Pre-existing, unchanged: Cloud Run's 16 GiB worker has room either way.

## Wave leftovers and failures of the first full run that were not wave C's

The first full run (10 failed, 5617 passed) found, besides wave C's own expectations (fixed above):
`tests/connectors/test_router_product.py::test_vendor_names_live_only_in_allowed_places` (a docstring in
`agents/naming.py` from wave B spelled a vendor domain; reworded; my own two test files also spelled vendor
names, now taken from the routing asset), `tests/guardrails/test_config_readers.py` (`jobs/snapshot.py` read
`export_rules.yaml` itself, in HEAD: it now reads through `jobs/config.py export_rule_texts`),
`tests/guardrails/test_route_sql.py` (wave B added raw SQL to `account_workspace.py`: one helper,
`archive.other_account_terms`, replaces two copies) and `tests/obs` `check_telemetry.py` (`jobs/repo.py`
used `logging.getLogger`: it takes an `on_archive_failure` hook like the people repository, the application
passes its logger). All four pass.

## Skipped (owner decisions or low value), and notes

* Extra product-only sheets and columns (Counts, Long text, Ref, Source code): left as they are, as instructed.
* Review findings 7 and 8 (the extra sheets, People/Tech archived links): see the decisions above.
* Origin check on authenticated POSTs (e2e D-10): a hardening item for the owner, not changed.
* Review 16 (the rank formula cell is formatted as Text), 19 (bidi controls in text cells; addresses reject
  them), 20 (the empty-sheet note sits at row 3 and says "None found"), 21 (font size and note heights),
  22 (platform sentences cut without a marker), 23 to 27 (web paging, prototype keys, aria labels), 17
  (hyperlinks over 2,079 characters: the 2,048 limit already drops them, and an archive address over it is
  now blanked): low severity, not changed. Finding 28 (the reference account's name in comments and two
  config strings) was fixed; 29 (freeze `C9` in the manifest) is noted in `docs/parity/reference_spec.md`.
* e2e D-6 (the stock hermetic provider can never produce a read posting: 191 characters under the 200
  minimum) and D-8, D-9, D-11 to D-15 are test-infrastructure or informational and unchanged.
* `EXPORT_RENDER_CHECKS` is still unset in the compose files (only Terraform sets it); local builds run no
  render checks, and the manifest now says so (`checks` lists no `workbook_recalc`).
* Deploy notes: the worker and API need the new image for the governor setting (`waiter_ttl_s`), the
  registration (`jobs/repo.py`) and the zip; a run saved before this release is repaired on its next
  rebuild. `data/releases/` makes the workspace zip larger (up to 2,000 files, 120 MB of text); the cap is
  `workspace.releases.max_files`.

## Files

Source: `src/account_intelligence/{urls,archive}.py`, `safety/{prefilter (new),client_output}.py`,
`replica/{sheets,table_export,workspace,file_tabs}.py`, `api/routes/account_workspace.py`;
`src/jobs/{repo,config,snapshot}.py`; `src/agents/{naming}.py`, `agents/production/pipelines.py`;
`src/exports/{contenturls (new),content,config,models,builder,verify,ooxml,obs,wiring,workspace,
workspace_files}.py`, `exports/excel/{common,jobs,people,tech}.py`, `exports/readers/{common,jobs,people,
tech,pg,exclusions,summary}.py`, `exports/word/render_cli.py`; `src/documents/deliverables.py` (comment).
Config: `config/{parallel,export_rules}.yaml`.
Web: `web/components/accounts/{SheetView,ArchiveView}.tsx`, `web/lib/server-api.ts`, `web/ui/components.css`.
Docs: `docs/guide/06-known-problems.md`, `docs/parity/reference_spec.md`; regenerated `docs/exports/files.md`,
`docs/architecture/loops.md`, `docs/api/openapi.json` (no route changed; the script rewrites it).
`.claude/launch.json`: entries `api-e2e-c` and `worker-e2e-c` for the reproduction.

Tests (new): `tests/exports/test_fix_wave_c.py`, `tests/replica/test_fix_wave_c.py`,
`tests/unit/test_prefilter.py`. Changed: `tests/exports/{test_db,test_scale,test_integration_checks,
test_evidence_part_c,test_evidence_parts,test_lying}.py`, `tests/replica/test_link_archive.py`,
`tests/jobs/test_archive_snapshot_db.py`, `tests/parallel/test_governor.py`,
`web/tests/unit/{sheet-view,server-api,archive-pages,research-screens-hardening}.test.ts*`.

## Commands and results

Python tests as `TEST_DB=ai_test_fixc PGPASSWORD=dev APP_RW_PASSWORD=... MIGRATOR_DATABASE_URL=... pytest
-m "not eval and not live" -q -p no:cacheprovider`.

* `ruff check src tests`: `All checks passed!`
* `mypy`: `Success: no issues found in 701 source files`
* `lint-imports`: `Contracts: 19 kept, 0 broken.`
* `scripts/gen_loops.py --check`: exit 0 (no output; it reported stale twice during the work, after loop annotations and removed log lines moved line numbers, and was regenerated)
* `python -m exports.docs --check`: exit 0 (`docs/exports/files.md` regenerated)
* `scripts/gen_jobs_docs.py --check`: exit 0
* `scripts/export_openapi.py` then `git diff --stat docs/api/openapi.json`: `wrote docs/api/openapi.json`; the file is identical before and after the run (same checksum); `1 file changed, 258 insertions(+), 1 deletion(-)` against HEAD is wave A and B's route changes, wave C changed no route
* `scripts/gen_web_client.py --check`: `generated client OK`
* `scripts/check_route_catalog.py`: `route catalog: every route has a policy, an audit class and a session tenant`
* `scripts/wiring_audit.py --check`: `wiring audit: 85 frontend call sites; no static breaks` (exit 0)
* `lint_migrations.py migrations`: `OK: 180 file(s) pass the isolation migration lint`
* `cd web && npx vitest run && npx tsc --noEmit && npx eslint .`: vitest `46 files, 279 tests passed`; `tsc --noEmit` exit 0; `eslint .` exit 0
* Full Python suite (about 64 minutes), first run: 10 failed, 5617 passed, 1 skipped, 1 xfailed (the causes
  are listed above and fixed); final run: **5627 passed, 1 skipped, 7 deselected, 1 xfailed, 0 failed** in 3915 s (1:05:15)
