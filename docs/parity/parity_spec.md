# Output parity: one bundle, the same files, tables and numbers everywhere

Status: build in progress, 2026-10-27. Scope: every account, every workspace. The reference
workspace (an account research finished by the skill on 2026-10-20) is only the golden set the
renderers are proved against; nothing in the product names it.

## What parity means

One research run produces one `AccountBundle` (JSON, schema
`src/parity/schema/account_bundle.schema.json`, version 1.x). The web screens render from the
bundle, the file renderers render from the bundle, the chat answers from the bundle. Files are
rendered once when a bundle version is finalised, stored with a manifest (name, size, SHA-256,
MIME type, bundle version, the recalculated Summary values), and a download streams the stored
bytes. A correction made on screen writes a new bundle version and renders again; a stored file
is never patched.

## The bundle is account-neutral

| Datum | Where it lives | Used for |
| --- | --- | --- |
| File name prefix, the Entity prefix of the account's own positions | `meta.account_short` | `{prefix}_Account_Summary.docx`, the `"{prefix}*"` COUNTIFS in the Jobs Summary |
| Legal name, domain, careers site | `meta` | titles, Part E, the people domain rule |
| Research date, pull date, 12 month window | `meta.run_date`, `pull_date`, `window`, `window_display` | subtitles, headings, the Jobs Summary title |
| Lanes (order of the lane tables), people lanes | `meta.lanes`, `meta.people_lanes` | Jobs Summary rows, Part C headings, Summary section 5, People Summary lane table |
| Entity labels and which count as the account | `meta.entities` | Summary section 5 base paragraph, Part H entity split, the Jobs counts |
| The pursuing client's display word and real name | `meta.pursuing_client_display`, `pursuing_client_internal` | every client file says the display word; the real name is scrubbed and gated |
| Analyst corrections (priority, lane, note) | `overrides` | applied after the rules, never written into code |
| Every count, total, share, rank | `computed` | the files, the screens, the chat; the number-consistency check compares prose to it |
| Every paragraph, bullet and table of prose, every workbook note | `prose` | the renderers print them; no renderer carries a hard-coded sentence about an account |

The reference workspace's scripts carried the glossary, the baseline bullets, the hiring
interpretation, the people interpretation, the workbook notes, the account name and absolute
output paths in code. The bundle builder (`parity.workspace`) lifts them out of a finished
workspace into `prose`, and for the reference they come from `build/prose_extra.json`, written
once by hand from the scripts (it sits beside the workspace, outside git, like the rest of it).

## Replay mode

`fixtures/workspaces/<name>/` holds a finished workspace in the skill's layout (`RESTORE.md`,
`deliverables/`, `build/`, `data/`, `notes/`). The folder is ignored by git: a workspace holds
real people's names and profile links, and the repository's tests use synthetic data only
(`docs/data-handling-people-data.md`). The replay check runs only when a workspace is present:

```bash
uv run python -m parity.workspace fixtures/workspaces/reference-ws fixtures/workspaces/reference-ws/bundle.json
uv run python -m parity.render fixtures/workspaces/reference-ws/bundle.json out/
uv run python scripts/parity/parity_check.py fixtures/workspaces/reference-ws/deliverables out/
```

`parity_check.py` is the owner's checker, unchanged. Synthetic bundles under
`tests/parity/fixtures/` exercise every renderer in the ordinary test run.

## Renderers

* Workbooks: Python `openpyxl` (`parity.excel`), one module per workbook, formulas kept as
  formulas and recalculated with LibreOffice headless so cached values exist.
* Word files: the `docx` npm package (`render/docx`, TypeScript), a port of the reference's
  `docx_lib.js`; the Account Summary, Sources and Evidence, Lane Highlights and the internal-only
  file. `docProps` timestamps are the run date, so a re-render gives identical bytes.
* One text cleaner in both languages (`parity.text.clean`, `render/docx/src/text.ts`): en and em
  dashes and ` - ` become `, `, a curly apostrophe becomes a straight one, `→` becomes ` to `,
  `**`, `##` and `#` are stripped from posting text.

## Quality gate (every render, blocks delivery)

Tool-name scan, citation and dash checks on the PDF text, rendered-row check, number
consistency (every number in prose equals `computed`), workbook checks (no formula errors,
formulas still formulas, cached values present, Summary values equal the count module),
confidentiality (allow-listed zip; no internal-only file; no pursuing client name, alias or
email address in `notes/` or `data/`), and data warnings (duplicate names with different
profiles, duplicate names in the exclusion list, empty columns, postings without a requisition).

## Decisions this build made that the brief did not settle

1. The internal-only file is named `{prefix}_Internal_Only.docx` (the reference called it after a
   person). The gate refuses any file name containing `Internal_Only`, or the first name the
   reference used for it (kept out of this repository's text: `config/skill/archive` holds it), in
   a zip or a share.
2. Workspaces and bundles built from real research stay outside git (`fixtures/workspaces/`).
3. The platform dictionary (31 platforms, their categories, lanes and patterns) is configuration
   (`config/parity/platforms.yaml`), shared by the rules, the ranking and the count module.
4. Entity tagging rules (a divested business, a subsidiary) are data in `meta.entities`
   (label, patterns, a close date), applied by `parity.rules.tag_entity`; the reference's rules
   are expressed that way in its `prose_extra.json`.
5. The live product's existing export pipeline (`src/exports`) keeps running until the bundle
   path is wired into the run (`agents/graph.py` build node); this build adds the bundle, the
   renderers, the gate and the replay check beside it.
6. Word renderer (`render/docx`), decisions of its build:
   * `docx` is pinned to 9.13.0, the release that was current on the reference's build date
     (nothing in `build/` names a version); `jszip` repacks the archive. Hyperlink relationship
     IDs are renumbered in document order and `docProps` timestamps and zip entry dates are the
     run date (`meta.window.to` at 00:00 UTC), so two renders of one bundle are byte-identical.
   * The zod schema is generated from the JSON Schema by `scripts/gen_bundle_zod.ts`
     (`npm run gen`); `format` (date, uri) is an annotation, as in JSON Schema 2020-12, and is
     not asserted. A bad URL fails the render (the source table reads the host from it).
   * Optional schema fields added for the Word files: `meta.source_ids` (feed, detections,
     teams, news_feed, live_board: the register IDs printed after the Part H headings and in
     the Summary section 5 base paragraph; a heading prints no bracket without one),
     `meta.part_c_lanes` (Part C lane order when it differs from `meta.lanes`, as it does in
     the reference), `meta.entities[].phrase` (how the base paragraph names an entity's removed
     postings, default "{label} positions") and `prose.hiring_base` (the whole base paragraph,
     used verbatim when present; otherwise the renderer composes it from `computed.ent_counts`,
     the entities, `computed.lane_rows_account` and `meta.source_ids.feed`).
   * `prose.evidence.news_events_heading` is printed when present; otherwise the heading is
     composed from `meta.source_ids.news_feed` and the event count. The other optional evidence
     paragraphs (`part_b_intro`, `part_c_closing`, `board_paragraph`, `part_g`,
     `detections_note`, `teams_note`) are skipped when absent.
   * The "Open on 19 Oct" column heading is `meta.pull_date_short` without its year; Part C
     headings use the short form whole. `meta.byline` is printed as a sentence (a full stop is
     added when it has none).
   * Neutral wording stays in the renderer: the section and part headings, the grade legend,
     the "Every statement carries a source ID" sentence, the empty-lane sentence of Part C, the
     Key people column headings, the tally line and the closing line of section 10.
   * Lane Highlights: portrait, title "{prefix}: leading opportunities and signals by lane",
     one Heading 1 per section with bullets, no tables, no page breaks. Internal only: title
     "Internal Only: {prefix} ({legal})", subtitle composed from `prose.internal_only.dates` and
     the byline, the first heading names `meta.pursuing_client_display`;
     `prose.internal_only.title_suffix` is accepted and not printed.
   * The renderer's own tests use a synthetic bundle (`render/docx/tests/synthetic.ts`) and
     read the files back with `jszip` and `fast-xml-parser`; the replay against the reference
     is `node render/docx/dist/cli.js fixtures/workspaces/reference-ws/bundle.json out/` and
     the owner's checker.

## Decisions of the Python build (bundle, rules, workbooks), 2026-10-27

6. Schema additions (all optional, `additionalProperties` still false): `meta.entities[].patterns`
   (case sensitive regular expressions on title and description) and `meta.entities[].before`
   (a dated rule for a business divested on that date), applied by `parity.rules.tag_entity` in
   list order; an entity without patterns is the default, the account itself.
   `prose.workbook_notes.people_security_line_template` (`{count}`, `{names}`),
   `prose.workbook_notes.people_security_line_at` (index among the People Summary notes,
   default 2) and `prose.workbook_notes.jobs_other_entities_label` (the Jobs Summary row that
   counts rows which are not the account's positions; default `Rows that are not {prefix}
   positions`). The Word build added `meta.entities[].phrase`, `meta.source_ids`,
   `meta.part_c_lanes` and `prose.hiring_base`; the models mirror them.
7. The models (`parity.bundle`) are strict: no coercion between strings and numbers, unknown
   fields refused, and an optional field the schema types without `null` may be absent but never
   null. `date` and `uri` formats are annotations on both sides (not checked). The one known
   gap: JSON Schema counts `2.0` as an integer, the models do not; no builder writes that.
8. Workbook cells use the dash rule only (`parity.text.clean_cell`): the golden Jobs sheet keeps
   ` - ` (2,116 cells), `’` (1,895) and `→` (10), and the Confirmed stack keeps `C#`. The Job
   Postings cells strip `**`, `##`, `#` first (`clean_posting`). The Word files use the full
   cleaner (`clean`: dashes, ` - `, apostrophe, arrow). The spec's "one cleaner" is therefore
   one module with the two forms the reference used.
9. The golden workbooks were written by openpyxl and re-saved by LibreOffice (their `app.xml`
   says so; a round trip of the goldens through the local LibreOffice 26.2 passes the checker).
   Every render ends with the same re-save (`parity.recalc`, one `soffice --convert-to xlsx`
   call for the three files, private profile, cleared environment, timeout), then a check for
   error values, lost formulas and missing cached values. The checker does not compare row
   heights, which the re-save sets.
10. The People Summary security line counts entries (rows), as the reference did ("29 rows"
    while the list holds 28 unique names: one person appears with two titles). Counting unique
    names would break parity; the gate's data warning names the duplicate.
11. The platform dictionary lives in `config/parity/platforms.yaml` (7 lane patterns, SAP case
    sensitive; 31 ranking entries; out of scope list enforced at load). The people rules
    (level, lane, priority, SI employers, security exclusion, platform keywords) are
    account-neutral regular expressions in `parity.rules`. `si_employers` writes `2015 to date`
    for a current role (the reference's replace dropped the space; no reference row was
    affected).
12. The workspace builder reads the prefix, legal name and both dates from `RESTORE.md`, the
    domain and the pursuing client from `notes/pull_log_phase1.md`, the window as the year up to
    the pull date, and lets the `meta` block of `build/prose_extra.json` override any of it
    (entities, lanes, Part C order, raw board counts, source IDs). The pursuing client's real
    name goes to `meta.pursuing_client_internal` and nowhere else; the replay test asserts it is
    absent from the rest of `meta` and from `prose`.
13. Analyst corrections in the reference (priority and lane fixes by name, notes from profile
    readings) are already applied in its `people_merged.json`, so the bundle rows carry them;
    `overrides` stays empty for the reference. A fresh run must write them as `overrides`.
14. `src/parity` is listed in the wheel packages of `pyproject.toml` so the images carry it.

## Wiring (2026-10-27): a product run produces and serves the bundle

The bundle path runs beside the export build, behind one switch, and serves the screens, the
stored data and the downloads from one version.

### The build

* `src/parity/from_run.py`: `build_bundle(SnapshotContent, RunFacts)`. The run's stored content
  comes through the export reader (`exports.readers.pg.PgContentReader`) and the export layer's one
  mapping (`exports.builder.map_snapshot`), so the labels, the row order, the entity rule, the
  requisition parsing and the citation registry are the ones the product already proves; the
  mapped models are translated into the bundle. `computed` is recomputed from the bundle rows
  (`parity.counts.jobs_summary` for the lane counts, so the Word file's hiring table equals the
  workbook Summary cell for cell; `parity.rules.tracker_tally`); every fixed sentence comes from
  `config/parity/texts.yaml` (placeholders are computed numbers and account names only) and the
  workbook notes from `config/export_rules.yaml sheet_texts`; the pursuing client's real name
  (the run's input, else the tenant settings) is written to `meta.pursuing_client_internal` and
  nowhere else, and the run's internal findings (`documents.graph.findings`) become
  `prose.internal_only` when there are any. `apply_overrides` applies an analyst's corrections
  (priority, lane, note by name) to the people rows, records them under `overrides` and
  recomputes the numbers.
* `src/parity/service.py`: `ParityService.render_version` renders a bundle as the account's next
  version: the workbooks (`parity.render`, recalculated with LibreOffice), the Word files (the
  `render/docx` command, `--internal` only when the bundle carries `prose.internal_only`), a
  minimal workspace folder (the client-safe bundle under `build/`, a run note under `notes/`) for
  the zip, then `parity.gate.run_gate` in process. Every file, the bundle document and the
  manifest are stored under `tenants/<tenant>/accounts/<account>/runs/<run>/parity/v<n>/` in the
  payload store; the row in `account_bundles` (migration `0088`, insert-only, owned like the
  account, RLS) carries the bundle key, the manifest, the gate report and the status: `built`
  when the gate passed, `failed` otherwise (then only the bundle document is stored and nothing
  of the version is served). Versions count per account under an advisory lock; a version is
  never rewritten.
* The graph's `build` node (`src/agents/graph.py`) calls `deps.parity.build(ctx)` after the
  export build and the client-output scan when `PARITY_BUNDLE=on`
  (`agents.production.parity_build.ParityBuildPort`, wired in `agents.production.wiring`). The
  outcome lands in the run's counters (`parity: {version, status}` or `{skipped: reason}`);
  a failed gate, a bundle the export mapping could not complete or a missing tool never
  changes the run's outcome: the legacy export files still deliver.
* Images: `docker/worker.Dockerfile` and `docker/api.Dockerfile` install `libreoffice-calc`
  beside the writer, build `render/docx` (`npm ci`, `tsc`, production dependencies only) into
  `/opt/render-docx` and set `PARITY_DOCX_CLI=/opt/render-docx/dist/cli.js`; `config/parity/`
  is read through `CONFIG_DIR`. The api image needs the tools because an edit renders in the
  API process.

### The routes (`src/account_intelligence/api/routes/parity.py`)

| Route | What | Roles |
| --- | --- | --- |
| `GET /v1/accounts/{domain}/parity/versions` | every version, built or failed, with the gate's failures | read_public |
| `GET .../parity/bundle?version=n` | the bundle document (the newest built version when no version is given) | read_public; the internal-only prose and the pursuing client's real name only for an admin |
| `GET .../parity/manifest?version=n` | the stored files (name, size, SHA-256, MIME) and the recalculated Summary values | read_public; the internal-only file listed for an admin only |
| `GET .../parity/screen?version=n` | what the account workspace draws in one read: meta, computed, ranking, the manifest summary, the tables of every file in document order with their columns and totals, the prose, the row fields the count module needs, the files, the zip link | read_public, same filtering |
| `GET .../parity/tables/{table}?version&offset&limit` | one page of a table's rows in the file's order and text (`parity.tables`), 100 rows by default, 500 at most | read_public |
| `GET .../parity/files/{name}?version=n` | the stored bytes as an attachment with the right MIME type, `Content-Length` and `X-Content-SHA256`; the internal-only file 404 for anyone but an admin | export_files (audited) |
| `POST .../parity/edits` | `{version?, overrides: [{name, priority?, lane?, note?}]}`: the next version, rendered again in the API process (503 `parity_unavailable` without the tools) | edit_shared_views (admin, analyst) |

The routes are in the catalog and the isolation matrix (table `account_bundles`, storage
layout `parity_version`); `docs/api/openapi.json` and `web/lib/gen/api.gen.ts` are regenerated.
The web proxy gives the files route the transfer timeout.

### The web (`web/lib/parity/`, `web/components/accounts/ParityWorkspace.tsx`)

* `tableSpecs.ts` is generated by `scripts/parity/gen_table_specs.py` from `src/parity/tables.py`,
  the one table definition the workbook renderers take their header rows from (the Word files'
  tables are declared there too and proved equal by the parity test); `clean.ts` is
  `parity.text`; `counts.ts` is `parity.counts` function for function.
* The account workspace reads `/parity/screen` when a built version exists and draws one tab
  per delivered file: the file's tables in its order through `/parity/tables/{id}` (Jobs and
  People paged on the server), the same columns, labels and sort, priority and rank chips in
  the workbooks' fill colours, the files' links (Open profile, Open posting, Profile), the Word
  files' prose above their tables, a download button per tab and "Download all" (the workspace
  zip, which never holds the internal-only file). The Summary counts are computed by `counts.ts`
  from the rows and asserted equal to the manifest's recalculated values; a difference shows an
  error banner (`data-counts="mismatch"`) instead of a number.
* `web/e2e/parity.spec.ts` drives a run over the synthetic world with `PARITY_BUNDLE=on`, then
  asserts every table on screen equals the rendered file's table (headers, row count, order,
  cell text, read back with `python -m parity.tables dump`), every count equals the manifest's
  cached value, every downloaded file's SHA-256 equals the manifest, and the zip has no
  internal-only member.

### Decisions the brief left open

1. The bundle's four posting classes are the reference's; a posting the reading left
   unclassified, one that names no lane platform and one read by title only are `User role`
   (the reference's "everything else") with the reading's label kept in the `what` column.
2. A posting's last seen date is the run date while it is open and its first seen date once
   closed: the job pipeline stores no last seen date.
3. `computed.excluded` carries the people module's removal counts (the rows it removed are not
   stored by name, except the security exclusions, which `people_excluded.security` lists as
   "Name (Title)"); the other `people_excluded` lists stay empty for a product run.
4. The Jobs Summary lanes are the parity dictionary's lane platforms
   (`config/parity/platforms.yaml`), not the export rules' matrix platforms; the ranking rows are
   the export layer's platform dictionary (`config/platform_dictionary.yaml`) as before.
5. The fixed Word prose (glossary, hiring interpretation, people text, interpretation bullets,
   cautions, Part G, the detections and teams notes) is template text in
   `config/parity/texts.yaml` whose placeholders are computed numbers only, so the number check
   holds for every account; the People List Summary notes drop the legacy export's search and
   record counts for the same reason (they are not in `computed`).
6. The number check now knows the tracker tally line's row total (pass 1 plus pass 2) and the
   Platform Ranking Counts formulas count the single cell when one platform is ranked
   (LibreOffice normalises a one-cell range on re-save, which the workbook check reads as a
   changed formula).
7. No share principal exists in the product (every reader is a tenant member on a session or a
   token), so "never on a share" is enforced as: the internal-only material only for an admin on
   their own session or token; a signed or shared link, when one is added, must not pass
   `may_see_internal`.
8. An edit renders synchronously in the API request (tens of seconds) rather than through the
   worker; the status set stays built or failed (no pending row). A queue through the worker is
   the alternative if edits become frequent.
9. The service reads the run's content through the export reader and the export layer's mapping
   rather than a second reading of the tables, so the two file sets of a run come from one
   mapping; when that mapping refuses a file (a content rule) the bundle is skipped for the run
   and the reason logged, the legacy build reports it.
10. `versions`, `screen` and `tables/{table}` are routes beyond the brief's four: the screen
    needs the table list, the totals and the count rows in one read, and the full bundle is too
    large to load for a page (every posting's description).
11. The synthetic bundle fixture gained the three pull sources its `meta.source_ids` cite
    (S3, S4, S5), so it passes the gate as a product bundle must.
12. The People and Jobs sheet formula ranges always span at least two rows (`$B$2:$B$3` when
    the sheet is empty or holds one row): a range ending on the header is re-ordered by
    LibreOffice onto the header (COUNTA counted it) and a one-cell range is normalised to the
    cell, both of which the workbook check reads as a changed formula. Found by the e2e run,
    whose synthetic research has no job postings.
13. The run's counters carry the outcome as integers (`parity_version`, `parity_built`,
    `parity_skipped`), because counters are summed across slices.
14. The e2e harness (`tests/e2e/serve.py`, `web/e2e/helpers.ts`) was stale against the current
    pages and the private-workspace policies: the alert seed and the drive now act as system
    jobs, the sign-in opens the tenant's SSO start through the web proxy, the harness runs
    with `LLM_FAKE=hermetic` (the fake model counts as set up) and `PARITY_BUNDLE=on`, and the
    spec creates its run through `POST /v1/runs` (the research form queues a job the harness
    does not run). The other two specs were already stale and are untouched.
