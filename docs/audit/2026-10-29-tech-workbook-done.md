# Tech Stack and Teams workbook: done note (2026-10-29)

Findings T1 to T5 of `2026-10-29-output-parity-audit.md`, built against the owner's reference
workbook and its build scripts (`platform_ranking`, `build_stack`). Nothing committed, no server
started, no research run, nothing deleted. The reference account is not named in code, config,
fixtures or tests.

## What changed

1. **Organisation tech stack is stored (T1).** New table `tech_stack_items` (migration `0090`,
   tenant and owner columns, row level security enabled and forced, `tenant_owner_isolation`
   policy, insert-only grant, unique on run, function, category, technology, `position` keeps the
   lookup's own order). New pipeline step `write_stack` beside `write_detections`; the research
   graph's `pipelines` node calls it once per run, in its own try block (a failure is logged and
   counted as `pipelines_stack_failed`, never touches the detections or ends the run). The step
   makes **no new paid call**: it first checks that this run (or its parent) already made the
   `org_tech_stack` lookup (exit `not_pulled` otherwise), then replays `org_resolve` and
   `org_tech_stack` from the ledger. `PipelinePort.write_stack` added to the port.
2. **Sheet 2 is `Confirmed stack`** with `Business function | Category | Technology | Postings naming
   it | Lane` (widths 26/40/34/18/26, title line, italic note merged A:E at height 40, freeze `A5`,
   filter `A4:E<last>`). Rows are the stack items in the lookup's order with the feed's own
   `job_post_used_count`. Lane = the last platform word found in the lower-cased name
   (`workbooks.tech.stack_lanes`: Salesforce, Salesforce (MuleSoft), ServiceNow, SAP, Tableau and
   analytics, Veeva, Outside the lanes (Workday); a startup check ties the keys to the platform
   dictionary). Out-of-scope names (`never_report`) are masked by the shared `ctx.rendered`; the
   note says "masked". There is no Ref column (the reference has none): row conservation reads the
   first three cells of each row (`verify.stack_key`, `xlsx_stack_keys`; builder tech branch), and
   those keys are the manifest `refs` of the tech file; manifest `rows_per_file["tech"]` is now the
   stack row count.
3. **Detections kept, but no sheet prints them.** `TechModel.rows` still holds the detections;
   Evidence part H is unchanged (its source codes are still cited from the tech mapper). The
   count lines were renamed `detections.*` (so the workspace RESTORE queries no longer call
   detections "rows in the file") and two lines were added: `file.rows` (stack rows) and
   `file.ranked`. Evidence part H "data pulls" therefore shows one more row (DP-0005 in the oracle).
4. **Platform Ranking as the reference (T3, T4, T5).** Header `In confirmed stack`, computed over the
   stack items' technology names (Not pulled when no stack). Lane words from the new
   `labels.tech_lane`. Dictionary patterns copied exactly from the reference script
   (`config/platform_dictionary.yaml`, version `2026-10-29.1`): Salesforce clouds and
   `salesforce cpq`, ServiceNow `service now\b`, Veeva eTMF/CTMS/`veeva vault`, Epic lookahead,
   `confluence(?! kafka)`, SAP case sensitive, SAS case insensitive (as the reference). Open now =
   postings not closed; Last seen = max(last seen, else first seen). Sort = postings descending,
   ties in dictionary order; Seen once in dictionary order, "None" when empty. Rank cells bold on
   the theme fills `rank_a/b/c` and under conditional formats with the same colours; Share is
   `0%` (theme `percent` kind changed from `0.0%`); per-column widths A 8, B 30, C 26, D 22, E 12,
   F 10, G 10, H 12, I 14, J 14; header height 45; notes merged A:J with estimated real heights;
   freeze `A9`; filter `A8:J<last>`; Counts block and Seen once block as the reference.
5. **Platform teams (T2, partly).** Reference layout built: title with "N largest of M matching teams,
   by postings since D" (when the content gives the counts), note merged A:H (height 40), header
   row 4 (height 28), widths 40/30/10/12/12/70/50/40, wrapped body with estimated row heights,
   freeze `A5`, no filter. Rows come from the new `SnapshotContent.teams` (`TeamRow`,
   `teams_matching`, `teams_since`); empty: the "None recorded" line. No provider call, no table.
6. Web tables: `views/rowsources/tech.py` now lists the stack rows (the table spec follows the new
   columns by itself).

## Files

Source: `src/exports/excel/tech.py`, `src/exports/readers/tech.py`, `src/exports/models.py`
(StackLine, TeamLine, TechModel), `src/exports/content.py` (StackRow, TeamRow, `stack`, `teams`,
`teams_matching`, `teams_since`), `src/exports/config.py` (TechBook.stack_lanes, Labels.tech_lane,
TechSheetTexts, tech lane validator, Workbooks ref rule relaxed for tech), `src/exports/verify.py`
(`stack_key`, `xlsx_stack_keys`), `src/exports/builder.py` (tech row-key check), `src/exports/readers/pg.py`
(stack read, `snapshot_counts["tech_stack_items"]`), `src/agents/production/pipelines.py`
(`write_stack`, `_stack_was_pulled`, `_insert_stack`), `src/agents/graph.py`, `src/agents/ports.py`,
`src/views/rowsources/tech.py`, `src/account_intelligence/api/routes/health.py` (EXPECTED_REVISION 0090).
Config: `config/export_rules.yaml` (`workbooks.tech`, `sheet_texts.tech`, `labels.tech_lane`),
`config/export_theme.yaml` (percent format), `config/platform_dictionary.yaml`, `config/ownership.yaml`.
Database: `migrations/0090_tech_stack_items.sql`, `.down.sql`, `versions/0090_tech_stack_items.py`,
`migrations/README.md`, `tests/isolation/matrix/matrix.yaml`.
Docs: `docs/exports/files.md`, `docs/exports/theme.md`, `docs/architecture/loops.md` (regenerated).
Tests: new `tests/exports/test_tech_workbook.py` (17), `tests/exports/test_tech_stack_db.py` (5);
updated `tests/exports/corpus.py`, `test_spec_v2.py`, `test_oracle.py` plus
`tests/fixtures/synthetic/exports/oracle_account.json` and `oracle_expected.json`, `test_db.py`,
`tests/agents/fakes.py`, `test_research_core.py` (+1 test), `tests/system/test_single_write_paths.py`.

## Tests

Run as `TEST_DB=ai_test_techwb ... pytest <paths> -m "not eval and not live" -q -p no:cacheprovider`.
- `tests/isolation`: 865 passed (matrix covers `tech_stack_items`; `gen_isolation_matrix.py --check`: every discovered object is covered).
- `tests/exports tests/agents` + `tests/system/test_single_write_paths.py` + three `tests/replica/test_file_*` files: 948 passed, 9 failed, 1 error before my last fixes. After fixing my three (row counts in `test_lying`/`test_scale`, a note loop in `test_spec_v2`), the files I touched pass: `test_lying test_scale test_spec_v2 test_tech_workbook test_tech_stack_db test_oracle test_db` = 105 passed. The other failures were transient or belong to the other agents' in-flight edits (`test_people_workbook`, `test_jobs_workbook` error, link archive rows in `test_evidence_register`; three of them passed again on a rerun) and `test_single_write_paths` (`link_archive` has no declared owner).
- Gates: `python -m exports.docs` and `scripts/gen_loops.py` regenerated (`--check` clean); `ruff` clean on my files.

## Open owner items

1. **Platform teams provider operation is not specified.** `OrgConnector._SUPPORTED` excludes it; no
   table, no pipeline. The sheet and model are ready: once an operation is specified, fill
   `SnapshotContent.teams`, `teams_matching`, `teams_since` (a reader for a `teams` table) and the
   rows appear. Until then the sheet prints "None recorded".
2. Ranking font size: the reference's Platform Ranking body and header are Arial 11 (the other two
   sheets 10); the theme has one size (10). Left as the theme.
3. The reference's rank note says "Matches found to be false on reading were removed"; the product
   keeps its own accurate sentence (window rule) because it removes nothing by reading.
4. Hermetic providers: the organisation lookup finds no match there by design, so the Confirmed
   stack is empty in local hermetic runs; no `org_tech_stack` cassette was added.
5. Raw `data/techstack_confirmed.json` in the workspace zip (the reference has it) is not written:
   `workspace_files.py` is outside this task.
6. `link_archive` has no owner entry in `tests/system/test_single_write_paths.py` (not mine, it
   fails today); deletion on request (`config/deletion.yaml`) does not list `tech_stack_items`,
   like `link_archive`.
7. Teams "First activity" prints blank when missing (the reference prints the word None, a bug of
   its script).
