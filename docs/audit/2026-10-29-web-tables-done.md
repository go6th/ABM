# Web tables: what was finished on 2026-10-29

Scope: the account workspace tables show an exact copy of the delivered Excel files, sheet by
sheet as tabs, every column, with working links, fills and buttons. Audit items W1 to W6 of
`docs/audit/2026-10-29-output-parity-audit.md`. Nothing was committed and no research was started.

## What changed (checklist against the code)

1. Client-output guard withholds a cell, never the page. `agents/naming.py`: a provider whose
   display name is an ordinary English word (`WORD_NAMES`, "Parallel") matches only as the vendor
   (capitalised inside a sentence, or followed by a product word such as "search API");
   "work in parallel" and "parallel processing" pass; every other provider name is still caught
   in any case. `scrub_client_text` is the per-cell form; `sheets.scrub_rows` and
   `file_tabs.scrub_blocks` use it for pages and tabs; a withheld cell reads the neutral marker
   (`sheets.WITHHELD`), a link to a provider host is dropped. `exports.policy.leak_hits` was
   checked: it uses the shared denylist (`parallel (search|fetch|ai|web)` only), so it already
   passes the English word (test added). The memo of the guard holds only texts up to 8,192
   characters (`MEMO_MAX_CHARS`).
2. A delivered account never falls back to the live sheet. `builder_main` accepts the file's header
   as names, in the configured order, with added or missing columns (`headers_agree`); `_judge`
   accepts a stored row with extra keys. Where the manifest count differs, `main_table` still
   shows the file's own table (`_file_main`, `main_rows`: count follows the same rule). A file
   with no manifest row count (not this builder's) still uses the stored table or live sheet.
3. Links on both screen paths. File-grid path: a url column shows the file's cell text and opens the
   hyperlink; two hyperlink columns in one row each keep their own key. Stored-table path:
   `sheets._link_text` takes the label from the workbook column's own `link_text` (config
   `workbooks.<kind>.columns[].link_text`), else the sheet text ("Open profile"), else the address.
4. Page weight: cells on a main-table page are cut at `CELL_CAP` (2,000 characters), recorded in
   `row.cut`; the web shows "Show all (n characters)" which fetches the whole cell from the new
   `GET /v1/accounts/{domain}/sheets/{sheet}/cell` route under the page's filter and sort. The
   guard scans the cut cells, once per distinct text (memo). Numbers below.
5. Row styling: `file_tabs.Cell.fill` is a theme token from the cell's solid fill, mapped through
   `theme.excel.fills` (priority_a/b/c, rank_a/b/c, new_row; hex is read from
   `config/export_theme.yaml` through `exports.config`, never in Python or TSX). A blank cell of a
   filled row carries the fill too (the whole row is highlighted). `SheetCell.fill`,
   `SheetRow.fills` are optional in openapi and zod. `web/components/accounts/cell-fill.ts` was
   reviewed (only known tokens become classes); `SheetView` and `TabView` render the class;
   `web/ui/components.css` holds the classes (no inline styles). The `new_row` colour in the CSS
   was wrong (a blue); it now equals the theme's value, and a test keeps the CSS equal to the
   theme.
6. Formula evaluator falls back to the file's cached value for a function it does not have (test
   with a re-saved file; error values are not values). The unstyled-header ordering bug is fixed:
   a blank-row gap closes the table before the next row is judged as a header (test).
7. Buttons on file-shaped tabs: the file grid now has the stored table's chips (Priority A,
   Meets seniority rule, the posting statuses; `sheets.chip_specs` is the one definition, matched
   to the file's columns by header), counted over the whole table, applied to the page, the cell
   route and the CSV and Excel exports. Sort headings, Show more rows, Download CSV and Open in
   Excel keep the filter and sort (web test). The CSV path is a transfer for the proxy timeout
   (`web/lib/proxy-body.ts`). The link archive (`attach_archives`, the `/archive` route,
   `tests/replica/test_link_archive.py`) still passes. No change to `contract.py` this session
   (the previous agent's contract change was already in `docs/api/openapi.json` and
   `web/lib/gen/api.gen.ts`; both `--check` gates pass).

## Files changed (my part; the tree also carries other agents' work)

Python: `src/agents/naming.py`, `src/account_intelligence/replica/file_tabs.py`,
`src/account_intelligence/replica/sheets.py`, `src/account_intelligence/api/routes/account_workspace.py`,
`src/account_intelligence/api/contract.py` (previous agent: cell route, fills, cut, archives).
Web: `web/components/accounts/SheetView.tsx`, `TabView.tsx`, `cell-fill.ts`, `web/ui/components.css`,
`web/lib/proxy-body.ts`, `web/lib/gen/api.gen.ts`. Generated: `docs/api/openapi.json`.
Tests: `tests/agents/test_naming_words.py`, `tests/replica/test_file_tabs.py`,
`tests/replica/test_file_sheets.py`, `tests/replica/test_workspace_speed.py` (one assertion: a
manifest that disagrees still shows the file's table), `web/tests/unit/sheet-view.test.tsx`,
`tab-view.test.tsx`, `v1-proxy.test.ts`.

## Before and after (reference Jobs sheet, 1,897 rows, longest cell 22,296 characters)

Script: `/tmp/claude-1000/-home-sakib-ABM-Generator/bef661d6-7235-4e44-9821-b38ffe9f59f4/scratchpad/measure_page.py`
(read with `python -I`; before = HEAD `naming.py`, whole-response guard, uncapped cells).

| Rows per page | Before: guard seconds | Before: bytes | After: guard seconds (cold) | After: repeat | After: bytes |
| --- | --- | --- | --- | --- | --- |
| 200 | 2.9 to 3.7 (the page was refused whole: 1 cell hit) | 2,075,145 | 0.37 | 0.00 | 603,984 |
| 500 | 6.4 to 6.9 (refused whole: 4 cells hit) | 4,367,829 | 0.50 | 0.01 | 1,494,645 |

After: 0 cells withheld on the reference sheet (the hits were the English word). Shaping a page
costs 0.01 s (chips read once per process; the first version re-parsed the YAML config per
request, 0.3 s, and was fixed). Reading the workbook itself is 0.8 to 1.2 s once per file (cached).

## Preview

DB `ai_tabs_ui` existed. It was at migration 0084, so it was upgraded to head (0090); the payload
folder of the old session was gone, so a synthetic Jobs workbook (the export test corpus) was
written back for the `highmark.example` Jobs deliverable and a scratch sign-in password was set for
the seeded demo user in that scratch DB. Servers `api-tabs` and `web-tabs` were started and stopped.
Seen in the browser: tabs (Summary, Jobs, Counts, Long text), chips (Open 14 filters to 14 rows),
`Link` shows "Open posting" and opens the posting, `Archived link` shows "Open archived copy",
`New since last research` column, new rows filled across the whole row. CSV with filter and sort,
Excel and a tab answered 200. Screenshot:
`/home/sakib/.claude/projects/-home-sakib-ABM-Generator/bef661d6-7235-4e44-9821-b38ffe9f59f4/tool-results/mcp-Claude_Browser-blob-1793270457160-vxgze0.jpg`.

## Tests

```
TEST_DB=ai_test_web PGPASSWORD=dev APP_RW_PASSWORD="$(grep '^APP_RW_PASSWORD=' .env | cut -d= -f2-)" \
 MIGRATOR_DATABASE_URL=postgresql://postgres:dev@localhost:5432/ai_test_web \
 .venv/bin/pytest tests/replica tests/api tests/agents/test_naming_words.py \
 tests/guardrails/test_config_readers.py -m "not eval and not live" -q -p no:cacheprovider
  -> 357 passed
cd web && npx vitest run      -> 45 files, 256 tests passed
cd web && npm run typecheck   -> clean
scripts/export_openapi.py --check, scripts/gen_web_client.py --check -> OK
scripts/check_guarantees.py -> 500 tags, 0 dangling
```

Other tests that use the naming guard (agents, documents, exports, isolation) were run: 397 passed,
3 failed. One of the three was mine (`test_yaml_readers_never_grow`, a new `yaml.safe_load` in
`file_tabs`) and is fixed. The other two are the workbook renderers' work in flight
(`tests/exports/test_lying.py::test_b4_detection_without_a_url_is_withheld`,
`tests/exports/test_spec_v2.py::test_ranking_sheet_is_first_and_its_formulas_are_registered`).

## What stays open

* `test_the_book_cache_is_the_tenants_and_reads_a_book_once` failed once in a full run while the machine
  was loaded by other test runs (it waits 0.2 s for a read to start); it passes alone and in the last run.
* `scripts/gen_loops.py --check` reports `docs/architecture/loops.md` stale and ruff finds style
  errors in `src/exports/word/*` and `src/exports/readers/pg.py`; none are in my files.
* Jobs `Link` still shows the address on the stored-table path until the workbook column carries a
  `link_text` ("Open posting") in `config/export_rules.yaml`; the code reads it from there.
* A file-grid page cuts cells at 2,000 characters; 500 rows with three long columns is still 1.5 MB.
* The dev scratch DB `ai_tabs_ui` now holds a synthetic Jobs workbook and a scratch password; the
  `.claude/launch.json` payload folder points at a session scratchpad path that I recreated.
