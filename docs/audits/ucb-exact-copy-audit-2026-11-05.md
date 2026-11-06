# UCB upload: why the product does not give back the workspace "same to same"

Date: 2026-11-05. Inputs: `~/Downloads/UCB_workspace.zip` (the owner's workspace, built in the
cloud) and `~/Downloads/UCB-everything.zip` (the product's "Download everything" for the account
built by uploading that workspace). The owner's rule: the upload must come back identical, every
word, no duplicate, nothing added, nothing dropped. Nothing was changed in code for this audit.

## 1. What IS identical

| Member of UCB-everything.zip | Result |
| --- | --- |
| `Deliverables/UCB_Account_Summary.docx` | byte-identical to the workspace file (md5 ef3c0062…) |
| `Deliverables/UCB_Job_Postings.xlsx` | byte-identical (05a0e99b…) |
| `Deliverables/UCB_People_List.xlsx` | byte-identical (06f3c3bc…) |
| `Deliverables/UCB_Sources_and_Evidence.docx` | byte-identical (dd6ba445…) |
| `Deliverables/UCB_Tech_Stack_and_Teams.xlsx` | byte-identical (3deaf996…) |
| `Internal - your files/UCB_workspace.zip` | byte-identical to the upload (08ab2288…) |

These survive only because `replica/downloads._fill` copies them as blobs out of the uploaded zip
(`_workspace_deliverables`, the 2026-11-05 fallback). They are not registered as the account's
deliverables in the database (no `deliverables` row is written by the import), which is why the
web screens cannot show them (section 3).

## 2. What is NOT identical: the `Tables/` folder

The product never copies the workbook sheets. It imports the rows into its native tables
(`documents/workspace_import.py`), then "Download everything" rebuilds every table from those rows
through the repair stage (`src/exports/repair/*`, spec `docs/repair/workspace_repair_spec.md`),
which has its own columns, its own rules and generates content of its own. Measured differences:

### People (owner `People` sheet 1620 rows, 17 columns → product `People.csv` 1611 rows, 13 columns)
- Columns dropped: `No.`, `Joined UCB` (1320 filled cells lost), `Current role since`,
  `Platform and method certifications` (145 filled cells lost), `LinkedIn activity, last 90 days`,
  `Second profile`.
- Columns added that the owner never wrote: `Entity` (= "UCB" on all 1611), `Last verified`
  (= 2026-11-05, the download day, on all 1611).
- Columns renamed: `Priority` → `Priority (A/B/C)`, `Former SI and consulting employers (firm and
  years)` → `Former SI and consulting employers`.
- `Level` rewritten on 1598 of 1598 shared people: "8 Manager" → "Manager", "6 Head" → "Head"
  (the number prefix is stripped by `repair/people._level` / `rules.level`).
- `Found by` rewritten on every row: "Title search (platform and leadership titles)" →
  "title_search"; "Named in research" → "mention"; "Technology association search" → "association".
- `Title as reported` changed on 14 people (whitespace collapsed, "and" → "&", one different title
  taken from another source: Don Chow "European/UK HR Manager" → "Group Human Resources Manager").
- `Priority` recomputed: Adrian Maynier C → B (`rules.priority(title)` outranks the sheet).
- 9 rows lost: 3 names re-spelled by whitespace collapse (Ben Jessie  MS, Bharath Ramaswamy,  PhD,
  Emmanuel de  Ryckel) and 5 people gone (Constantinos NIKOLETOS, Panayotis Valmas, Pankaj Kalambe,
  Tina Kimbell, Tina Oelbrandt) to the importer's de-duplication (`merge_people`) or the repair
  stage's name/URL dedupe.
- `Former SI and consulting employers` now lists every past employer (Procter & Gamble, Heineken,
  Teva, Randstad, UCB itself) where the owner listed SI and consulting firms only.
- The owner's `Summary` sheet (27 rows: method, exclusions, seniority rule, 23 named exclusions) is
  not in the product at all. Product adds a `Leadership` sheet and an `About` sheet.

### Job postings (owner `Jobs` 92 rows, 13 columns → product `Job postings.csv` 85 rows, 17 columns)
- The owner's row unit is one listing (same requisition in several cities = several rows). The
  product's rule is one row per posting ID, so 92 becomes 85, plus a second sheet `All postings
  (unfiltered)` of 94 that the owner does not have.
- Columns dropped: `Requisition` (renamed `Requisition ID`), `What the posting says (read)`, `Link`.
- Columns added: `Posting ID`, `Entity`, `Lane(s)`, `Posted`, `Source URL`, `Collected`.
- `Status` recounted: Open 46 / Closed 46 → Open 42 / Closed 43.
- `Class` recomputed from the text instead of the sheet: Working tool 49 → 45, Outside the lanes
  28 → 25, User role 7 → 8, Build role 3 → 2.
- `Title` changed on 3 shared requisitions (93290 "Digital Technology Lead Process Excellence &
  Autom…" → "DevOps Automation Lead"), `Platforms named` re-detected on 2 (92599 "Power BI" →
  "Tableau, Power BI"), `Seniority` rewritten on 6 ("" → "Not found", "mid_senior" → "manager").
- The owner's `Summary` sheet (platform counts, class definitions, false-match notes) and `Hidden
  title roles read` sheet (12 rows) are not in the product.

### Tech stack and teams
- `Platform Ranking`: 17 rows → 12; columns `Last seen` and `Matches checked by reading` dropped.
- `Confirmed stack`: 70 rows → 228. The product adds 166 technologies from website detections
  (AJAX, Adobe, Acquia, AddToAny, Agentforce, AWS, …) into the owner's curated confirmed stack and
  renames `Postings describing it as used` → `Postings naming it`, adding 7 columns.
- `Platform teams`: same 25 teams, but all 7 of the owner's columns are replaced by 4 others.
- `Technology detections` (199 rows) is dropped as a sheet.

### Signals, Sources, News, Web searches, Coverage
- Signals: the owner's Account Summary holds 34 rows (four `Signal | Grade | Lanes | Date | Source`
  tables + one `Finding` table). The product writes 56 signals it generated itself
  (`repair/signals.build_signals`: "Platform hiring spike", "Executive speaking", …) and logs 2 of
  its own as dropped "no source". None of the owner's wording is in the table.
- Sources: the owner cites 54 `S` codes and 16 `J` codes. The product writes 195 rows renumbered
  `S-0001…S-0195`, so the `[S12]` citations in the docx no longer resolve against the table
  (its own check `citations_resolve` reports "0 cited codes").
- News: the owner's `news_events.json` has 64 events. The product writes 147 rows, all graded
  `Lead`, from the raw feed.
- Web searches: 119 = 119 (the one table that matches in count).
- Coverage: rebuilt (33 rows) and then its own check `coverage_agrees_with_deliverables` FAILS
  against the owner's docx, because the product's coverage disagrees with the owner's.

## 3. The web screens

`replica/file_tabs.delivered_file` and `sheets.delivered_run` read `FROM deliverables`. The import
writes no `deliverables` row (only `internal_deliverables` for file findings), so an imported
account has no "delivered run". The People / Job postings / Tech screens therefore show the native
rows (`replica/research_tables.native_tables`) with the product's columns and the rewritten values
of section 2, never the owner's sheets. The "workbook sheets as tabs from the delivered file"
feature (2026-10-27) cannot engage.

## 4. Root cause, in one sentence

The product treats the uploaded workspace as raw material to be re-derived (rows → native tables →
repair stage → its own schemas and generated content), never as a finished answer to be preserved;
only the five deliverable blobs escape that path, by copy.

Three design choices make it so:
1. `workspace_import` keeps only the columns it has a native column for, and normalises
   (level codes, lanes, de-duplication, whitespace) on the way in.
2. `repair` ("applies to every workspace, present and future", spec of 2026-10-30) is run on
   imported accounts as well, and its job is to rebuild, re-tag, re-grade and generate.
3. The import does not register the zip's workbooks as the account's deliverables, so neither the
   screens nor the download can use the "show the delivered file itself" path that already exists.

## 5. Plan and logic (nothing built yet; waiting for the owner's go)

Decision to take: an account built from an uploaded workspace is in **exact-copy mode**. The
workspace is the answer. The repair stage is for accounts the product researched itself.

A. **Register the workspace as delivered.** At import, write one `deliverables` row per deliverable
   member (kind people/jobs/tech/summary/evidence, manifest with sheet names and row counts) under
   the file-import run. Effect: `delivered_run` exists, the screens open the owner's workbooks as
   tabs (existing `file_tabs`), counts come from the sheets. No new screen code.

B. **Store every sheet verbatim.** Alongside the native rows, store each sheet of each workbook as
   it is (sheet name, header row, every row, every cell, in order) in `document_tables` /
   `document_table_rows` (they exist). Native tables stay for search and Ask, but display and
   export read the verbatim copy. Nothing is dropped: Summary, Hidden title roles read, Technology
   detections, Leadership, all columns incl. `No.`, `Joined UCB`, certifications.

C. **Download everything in exact-copy mode.** When the account has a file-import run and no
   research run: skip `RP.repair`. `Tables/` = each verbatim sheet as CSV and XLSX, one workbook per
   deliverable, same sheet names and order. Signals = the docx's Signal and Finding tables, row
   for row. Sources = the docx's source list with the owner's codes (S1…S54, J1…J16). Web searches,
   News, Coverage = the workspace's own `web_searches.json`, `news_events.json` and the docx
   tracker, as written. `Logs/` = an import report (file rows = rows stored, per member) instead
   of a repair report. The client-file safety scan still runs (it only refuses, never rewrites).

D. **Import keeps the words.** For the native rows (used by Ask and search): keep `Level` as the
   sheet's string, trust `Class`, `Priority`, `Status`, `Title`, `Platforms named`, `Found by` as
   written, no re-detection, no `rules.*` recomputation, one row per sheet row (no `merge_people`
   / `merge_postings` folding; only byte-identical duplicates merge, and the merge is logged).

E. **Proof.** A golden test: import `UCB_workspace.zip` on a scratch DB, run Download everything,
   compare every `Tables/*` sheet cell by cell to the workspace's workbooks and docx tables.
   Required result: zero differences, 1620 people, 92 jobs, 34 signals, 54 S + 16 J sources.
   The comparison script from this audit becomes that test's checker. The synthetic owner-shaped
   fixture (`tests/documents/workspace_zip_owner.py`) gets the missing sheets.

Order of work: A and B first (one change in `workspace_import` + one in the document service),
then C (`downloads._fill` branch + a verbatim packager), then D, then E. A, B, C give the owner a
same-to-same download and screens; D only changes what Ask reads.

Not in this plan: changing the repair stage for researched accounts, or any paid run.

## 6. Status (2026-11-05, evening): built, uncommitted, not deployed

The owner's correction: the fault is the **upload**, not the download. Rebuilt in that order.

What the probe on the real `UCB_workspace.zip` found beyond section 2 (scratch database, the
upload path of the product): the Standard redaction's national-ID pattern rewrote 21 cells of
the deliverables ("Pankaj Kalambe" → "Pan[NATIONAL_ID_7]", LinkedIn slugs after "pan"/"tin"), so
4 people failed the name check; 5 people with one name and two profile links were folded; 6
postings the sheet lists twice were folded; 3 archive records without a link were dropped; the
Hidden title roles sheet's 12 rows were imported as postings (kept: they are postings).

Built:

- `documents/redact.py`: the national-ID label is a whole word and the identifier must carry a
  digit.
- `documents/chunking.py` + `service.py`: the deliverable members of a workspace package are
  stored as written (`Chunker.verbatim_members`); the tenant's Strict tier still applies.
- `documents/workspace_import.py`: `register_deliverables` (the five files as the account's
  `deliverables` rows under the file-import run, manifest `source: workspace_import`);
  `keep_rows` (one posting row per sheet row, a repeated key gets `#<sheet row>`); `merge_people`
  folds only an identical profile link; linkless rows are stored (migration 0111 lets a
  file-import posting have no address).
- `replica/sheets.imported_run`, `file_tabs.fresh`, `workspace.newest_files/stale_kinds/
  deliverables`, `routes/account_workspace`: an imported run is never materialised or rebuilt;
  its files are shown under any exclusion set.
- `replica/downloads._fill_imported` + `documents/exact_copy.table_members`: Download everything
  for an imported account ships the files, each workbook as uploaded, each sheet as CSV, each
  Word file's tables as a workbook and CSVs, and `Logs/import_report.md`; the repair stage does
  not run.
- `documents/exact_copy.report`: the cell-by-cell checker (store, registered files, native rows).
- Tests: `tests/documents/test_workspace_exact_copy_db.py` (synthetic golden test),
  `tests/documents/test_exact_copy_real_zip.py` (the owner's package, `EXACT_COPY_ZIP=...`
  `EXACT_COPY_DOMAIN=ucb.com`), mapping test updated, owner fixture gained a linkless row.

Real-zip result after the rebuild: every sheet of every workbook and every table of both Word
files stored cell for cell; all five files registered with the package's SHA-256; people
1620 = 1620, postings 104 = 104 (92 Jobs + 12 Hidden title roles), nothing folded.
