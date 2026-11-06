# Plan: workspace import (a research zip read into its proper shape)

Written 2026-11-03 from `docs/audits/workspace-zip-ingest-gaps-2026-11-03.md`. Goal: when a client
uploads a research-workspace zip (the shape of `BD_workspace.zip`), every member is stored as its
own file, every deliverable lands in the native tables the research graph writes, the captured
pages become archive copies, the notes and pull log are kept and shown, and the owner-only file is
hidden. Nothing here calls a model or a provider; the import is free.

## The rule the whole plan serves: open everything, store everything, show everything

Owner's requirement (2026-11-03): when a workspace is uploaded, the product opens every file and
every page inside it, stores each thing in the database in its proper shape (a table stays a
table with its rows and columns, a page stays a page, a link stays a link that opens, an archive
copy stays an archive copy), and shows all of it in the account's deep dive. Nothing is reduced to
searchable text only, and nothing is silently dropped.

This is a **completeness contract**, checked by the import itself:

| In the zip | Stored as | Shown in the deep dive as |
|---|---|---|
| A workbook sheet with a header row | one table with every column and every row (`document_tables` for the original, plus the native table for the sheet's kind) | a sheet tab with the same columns, filter and sort, link cells clickable |
| A cell that is a hyperlink | the URL on the row (`url`, `profile_url`, `link`) | a link that opens the page (or the archived copy when the page is gone) |
| A formula cell | its cached value and the formula text (`document_tables.columns[].formula`) | the value; the file tab shows the recomputed matrix |
| A Word document | its sections as prose and **its tables as tables** with hyperlinks kept | a document view page by page (section list on the left, tables rendered as tables, links live) plus its rows in the native tables (sources, facts, sections, tracker) |
| A captured page (`data/releases/*.md`) | a `link_archive` row (url, title, date, kind) and the body in the payload store | an entry on the `/archive` page that opens the copy; the Sources sheet links to it |
| A raw pull JSON | the records in the native table for its kind (detections, news events, profiles, posts, teams, connections) and the original in `document_tables` | the live sheet for that kind (Technology, News, People, Teams) |
| A note or the pull log | `research_notes` / `pull_log_calls` rows | a Notes tab and a Calls sheet |
| A build script | recorded as a member, not parsed | the Files page member list, status "not read (build script)" |
| The owner-only file | stored with `audience=owner_only` | visible to the owner and admins only |
| Every member | one `document_members` row with status read / skipped / failed and the counts it produced | the Files page: one line per member with its counts and a link to where its data landed |

**Reconciliation**: at the end of the import the service writes `documents.import_report`
(jsonb): per member, rows in the file vs rows stored vs rows shown. A member whose stored count is
below its file count is marked `partial` with the reason, and the Files page shows it. The import
is not "done" until every deliverable sheet, every captured page and every raw pull reconciles; a
docx reconciles when every table in it has a table unit and every hyperlink has a target. The
hermetic test for each phase asserts this report against the fixture zip.

## De-duplication on import (owner's requirement, 2026-11-03)

Repeated rows, repeated job openings and repeated links are merged while the zip is read, so each
thing exists once in the database and once on the page. Merging never deletes: the surviving row
keeps a `merged_count` and the member report lists what was folded in, so the reconciliation still
adds up (file rows = stored rows + merged rows).

| Kind | Duplicate key, in order | What the survivor keeps |
|---|---|---|
| Job posting | 1. requisition id (`R-…`); 2. normalised posting URL; 3. title + location + entity when both have no id and no URL | earliest `first_seen`, latest `last_seen`, status Open if any copy is open, the union of platforms, sentences and observations, the fullest description; the other URLs go to `job_postings.other_urls` |
| One requisition posted in several locations (the reference's Talent Acquisition Technology Architect, eight rows) | requisition id | one posting with `locations[]` (new column, 0104) and `location` = the first; the Jobs sheet shows "8 locations" |
| Person | 1. canonical profile URL; 2. name + employer domain; 3. name + title when neither has a link | the row with the newest title or dates; the other profile URL goes to `people.second_profile_url`; `people.merged_into` on the folded row (exists already) |
| Link / archived copy | `url_normalized` (fragments such as `#assets…` and tracking parameters stripped, `www.` and trailing slash ignored) | newest capture; `link_archive` is insert-only, so the index points to the newest copy (already its rule) |
| Captured page saved twice under two names | same as link | one `link_archive` row, both member paths in the member report |
| Source (S register) | `url_hash` | one `sources` row; the S numbers that pointed to it are kept in `sources.aliases text[]` (0106) |
| Technology detection / stack item | technology name (case-folded) + category | highest `source_count`, latest `last_seen`; a technology repeated across categories (Databricks three times in the reference) stays once per category, since the category is part of the key |
| Team | team name (case-folded) | the fuller row (more technologies) |
| News event | event id, else headline + effective date | the earliest found_at; later copies add their source URL |
| Table rows in `document_tables` (the raw copy of the file) | **not de-duplicated**: the original file is kept as it was, so the merge can always be checked and reversed |

Rules: comparison is case-insensitive and whitespace-folded; a key that is blank is skipped, never
matched to another blank; a merge is logged as a `person_events` / `job_posting_observations` row
or in the member report with both source rows; the import report shows per member "rows 1,897,
stored 1,889, merged 8". On the BD reference this folds the 3 repeated requisitions, the 5 pages
saved twice, the 3 same-person profile pairs and the 8-location requisition.

Ordering rule: each phase ships on its own, is idempotent per document and member (`write_key`),
and respects the one-writer-per-table rule (`tests/system/test_single_write_paths.py`): new rows go
in through the owning repo, never from `documents/`.

## Phase 0. Make the zip safe to import (1 day)

Fixes the three things that can wreck the upload before anything new is built.

1. **Member routing by path and name, not by header matching.** New module
   `src/documents/workspace.py`: `classify_member(path) -> MemberRole` with roles
   `deliverable_summary`, `deliverable_evidence`, `deliverable_people`, `deliverable_jobs`,
   `deliverable_tech`, `owner_only`, `note`, `restore`, `captured_page`, `raw_pull`, `build`,
   `other`. A zip is a *workspace* when it holds `RESTORE.md` or `MANIFEST.json` at its root (or
   one level down). Rules: `deliverables/*_Account_Summary.docx` etc. by suffix; `*_the owner_Only.docx`
   and anything under `internal/` → owner_only; `notes/*.md` → note; `data/releases/*.md` →
   captured_page; `data/*.json|*.txt` → raw_pull; `build/*` → build.
2. **Skip `build/` and the deliverables importer on raw pulls.** `deliverables.import_document`
   only looks at tables whose member role is a deliverable workbook. Removes the three-fold
   duplicate import of `jobs_all.json` / `jobs_bd.json` / `jobs_lane_rows_final.json`.
3. **Stream object-shaped JSON.** `research_files/parsers/tabular.py::JsonParser._document`: when
   the top level is an object with one list value larger than `json_max_document_bytes / 4`,
   stream that list (`ijson`-style or a two-pass scan for the key) in 5,000-row units instead of
   loading the whole document. Add a test with a 70 MB synthetic `{"jobs":[...]}`.
4. **Owner-only members are not read.** A member the layout marks owner-only (`*_Only.docx`,
   `*_Internal_Only.docx`, anything under `internal/`) is listed and counted as skipped with the
   reason "an owner-only file; upload it on its own", never stored as passages. The
   `documents.audience` column first planned here moves to phase 1: until members are documents
   of their own (`document_members`), a per-document audience cannot hide one member of a zip.

Done when: the BD zip uploads, indexes without a failed part, imports each posting once, and the
owner-only file is listed as skipped and stored nowhere.

**Status 2026-11-03: phase 0 built and green** (`src/documents/workspace.py`; `service._read_part`
skips by role; `deliverables.SheetReader` imports deliverable members only; `JsonParser._document`
caps units by bytes; tests `tests/documents/test_workspace_roles.py`,
`tests/documents/test_workspace_zip_import_db.py`, the object-JSON case in
`tests/research_files/test_parsers.py`). Not committed.

## Phase 1. Members as files (2 days)

Migration `0103_document_members.sql`:

```
document_members(id, tenant_id, document_id, member_path, role, declared_type, sha256,
                 bytes, status (read|skipped|failed), failure, passages int, table_id,
                 created_at)  -- RLS, owner documents/repo.py, unique (document_id, member_path)
```

`archives.py` already streams members; `documents/service.py::_read_part` records one row per
member from the per-part `evidence.members_read / skipped`. The Files page
(`web/app/(client)/accounts/[domain]/files`) gets a member table (path, role, status, size) from a
new `GET /v1/documents/{id}/members`. `filemap.render` lists members by role instead of
"(N files)".

**Status 2026-11-03: phase 1 built and green.** Migration `0102_document_members.sql` adds
`document_members` (path, role, declared type, bytes, status read/skipped/failed, failure,
passages, tables, audience, part) and `documents.audience`, and redefines `erase_rows` with the
new table; `documents/repo.py` writes the rows (`record_members`, `members`, `set_audience`);
`service._read_part` records every member it read or skipped, `_decide` records the members of a
failed part as failed; `start_upload(audience=)` and `POST /v1/documents/uploads` take
`audience` (client or owner_only), the status view shows it and an owner-only document never
feeds the deliverables import; `GET /v1/documents/{id}/members` lists the rows; the uploads card
opens the list (`web/components/accounts/ZipMembers.tsx`); `list_sources` carries each member's
role and the Ask file map counts members by role. The deep dive has an **Uploaded files** sheet
(`/accounts/{domain}/uploaded-files`, `replica/sheets.py` key `uploaded-files`: file, uploaded as,
what it is, read/skipped/failed, why not, passages, tables, bytes; filters by status and
deliverables) with its count on the account page rail (`counts.files`), so every file of a
workspace zip is seen in the deep dive. Migration 0102 also redefines `purge_deleted_account`
(0096) so an account purge erases the member rows. Not yet: the "owner only" option in the upload
form, and a member-level audience filter on Ask reads (RLS already limits documents to their
owner in user scope; the skip at read time keeps owner-only members out of the store). Not
committed.

**Status 2026-11-03, later: phases 2 and 3 built and green on the database test**
(`tests/documents/test_workspace_zip_import_db.py`: postings, people, stack, ranking, teams
from the workbooks; sources with aliases, graded facts, report sections, tracker rows and
people notes from a synthetic Account Summary). Phase 3 adds migration 0104 (sources
how_retrieved / what_it_supports / aliases, five section kinds, coverage_rows needs /
what_remains), the Word parser's tables-as-rows and links-as-``label (url)``,
`documents/workspace_docx.py`, `grading.repo.record_imported_fact`, `db.source_ids.import_source`,
`tracker.repo.CoverageRepo.import_rows`, `agents.production.state.import_sections`, and a
``runs`` row for the file-import run. Findings along the way: a posting row needs a web address
(rows without one are counted as not imported); the import writes as the account's owner;
merged prose passages carry their member under ``from``/``to``.

**Earlier note (session cut by the usage limit): phase 2 code written, not yet verified on
the database.** Migration `0103_platform_teams_and_import_columns.sql` (platform_teams, the
import columns on job_postings / people / tech_detections, the reconciliation counts on
document_members, erase and purge functions, eraser grant) round-trips and its registry tests
pass. `documents/workspace_import.py` maps the sheet rows to native records (unit test
`tests/documents/test_workspace_import_mapping.py` green), writes through `jobs.repo.import_postings`,
`people.repo.import_people` and `agents.production.pipelines.import_stack_items /
import_detections / import_platform_teams`; `deliverables.import_document` routes a workspace
package to it; the document service passes the payload store so posting texts are kept. Left:
run `tests/documents/test_workspace_zip_import_db.py` (updated for native rows) and the
documents/replica folders on a scratch database and fix what they find; four E501 long lines in
the mapping test; the live Teams sheet and the export readers for `platform_teams` (phase 5).

## Phase 2. Workbooks into the native tables (4 days)

Replace the facts-only import for a workspace zip with writers through the owning repos. Keep
the facts path for a loose workbook upload (today's behaviour).

| Sheet | Writer | Rows | New columns needed |
|---|---|---|---|
| Jobs | `jobs/repo.py` (new `import_rows(txn, account, run, rows)` next to `register_posting_sources`) | `job_postings` (posting_key from requisition or url hash, title, location, first_seen, last_seen, status, seniority, posting_class, platforms[], lanes[], url, full_text_read), `job_posting_platform_sentences` (split "Platform sentences" on `\|\|` and `: `), `job_posting_classifications` (class, classified_by='import', evidence_sentence = "What the posting says") | `job_postings.entity_tag text`, `job_postings.summary_read text` (migration 0104) |
| People | `people/repo.py` (new `import_rows`) | `people` (person_key from profile url, name, title, job_level mapped from the 9 levels, lanes[], priority, scope_status from "Meets seniority rule", location, origin from "Found by"), `person_profiles` (prior_employers from "Former SI employers"), `person_sources` (origin=title_search/association/mention from Found by) | `people.note text`, `people.second_profile_url text` (0104) |
| Confirmed stack | `agents/production/pipelines.py` (expose `record_stack_items`) | `tech_stack_items` (business_function, category, technology, postings) | none |
| Platform Ranking | same | `tech_detections` (technology, last_seen_at, source_count = postings) | `tech_detections.open_now int`, `in_confirmed_stack bool`, `checked_by_reading bool` (0104) |
| Platform teams | new table | `platform_teams` | migration 0105 (below) |
| Summary sheets | not imported | the live sheets recompute counts; the exclusion list goes to `people` with `excluded=true, excluded_reason` | none |

Migration `0105_platform_teams.sql`:

```
platform_teams(id, tenant_id, account_id, run_id, team text, function text, postings_count int,
               first_activity date, last_activity date, lane_technologies text[],
               technologies jsonb, locations jsonb, source_ref text, write_key text unique)
```
Owner: `agents/production/pipelines.py` (so the research graph can fill it later from
`teams.json`-shaped pulls). `exports/content.py` reads it instead of the empty tuple, which also
fixes "No pipeline stores teams yet" for live runs.

Account run: the import runs under one `account_runs` row (`input_mode='file'`,
`finished_reason='workspace_import'`) so every row carries a `run_id` and the sheets, the
"What changed" card and the exports pick it up like a research.

Done when: the deep-dive sheets show 1,659 BD postings with class and sentences, 736 people with
level/lane/priority/SI employers, 212 stack rows and 25 teams, all attributed to the uploaded file,
and a rebuilt People List / Job Postings / Tech Stack from the product matches the reference
columns (parity check `scripts/parity/parity_check.py`).

## Phase 3. Word deliverables into sections, sources, facts and tracker (4 days)

Parser change first: `parsers/documents.py::DocxParser` emits a `table` unit per `w:tbl`
(rows of cells, header = first row) and keeps hyperlink targets (`r:id` → rels) as `[text](url)`.
Prose keeps the heading sections. Then `src/documents/workspace_docx.py` reads the two files by
their known structure (`config/output_spec.yaml` names the sections):

| Summary section | Target |
|---|---|
| 1 picture / stands out / lane meaning / how this could be wrong / sharpen | `report_sections` kinds narrative, stands_out, lane_meaning, caveat, plus new kind `sharpen` (writer `agents/production/state.py`) |
| 2 Recent news | `news_events` (Phase 4) with `why_it_matters` |
| 4 Signals tiers | `facts` through `grading/repo.record_document_fact` with the row's grade mapped to `product_grade` (Confirmed→confirmed …), lane, date, cited S codes resolved to `sources` |
| 5 Platform picture | `facts` (one per lane item) |
| 6 key people / status changes | `people.note` for key people; `person_events` for status changes |
| 7 hypotheses + confirming questions, cautions; 8 questions | `report_sections` hypothesis (confirming_question), caution (new kind), first_call_question |
| 9 tracker | `coverage_rows` via `tracker/repo.py` with new columns `needs text`, `what_remains text` (0106) |
| 10 source links + Evidence Part A | `sources` via `db/source_ids.get_or_create_source` with new columns `how_retrieved`, `what_it_supports` (0106); keep the S number as `source_code` |
| Evidence Part B/C (J extracts) | `job_posting_classifications.evidence_sentence` on the matching posting; J id kept in `job_postings.row_json.j_id` |
| Evidence Part H detections/news/teams | `tech_detections`, `news_events`, `platform_teams` |

Done when: the account page's one-minute version, Findings, Sources (with How retrieved) and
Coverage sheets are populated from the uploaded docx without a run.

## Phase 4. Captured pages, raw pulls, notes (3 days)

1. `data/releases/*.md` → `link_archive` through `archive.py::record` (kind from `link_kind(url)`,
   title/date from the header lines, body stored in the payload store with `archive_body`). The
   `/archive` screen then lists them.
2. Provider-shaped importers in `src/documents/raw_pulls.py`, each a thin mapper into the owning
   repo: `techstack_detections.json` → `tech_detections` (add `score numeric`, `seen_on_postings`,
   `seen_on_pages`, `behind_firewall`, `locations jsonb` in 0106); `news_events.json` →
   `news_events` (new table 0106: event_id, category, headline, summary, found_at, effective_date,
   confidence, source_url, location, related jsonb; owner `signals/events.py`); `profiles_full.json`
   → `person_profiles`; `posts_comments.json` → `person_posts` + new `person_comments`;
   `connections.json` → new `org_connections`; `teams.json` → `platform_teams`;
   `techstack_confirmed.json` → `tech_stack_items`; `org_signals.json` → `signal_events`.
   `jobs_12mo.json` is not re-imported when the Jobs workbook exists (same postings); otherwise it
   feeds `job_postings`.
3. `notes/*.md`, `RESTORE.md` → new table `research_notes` (account_id, document_id, kind, text);
   the pull log's call table → `pull_log_calls` (step, tool_role, parameters, result_count,
   output_file). Owner: a new `src/research_notes/repo.py`. Tool names are already redacted by the
   skill; run `safety.client_output.scan_text` on every note before storing and withhold on a hit.

**Status 2026-11-03, evening: phase 4 built and green on the database test.** Migration 0105
adds `news_events` (with an `event_key` for the unique index), `person_comments`,
`org_connections`, `research_notes` and `pull_log_calls` (app_rw needs USAGE on the two
bigserial sequences). `documents/workspace_raw.py` reads the captured pages, raw pulls, notes and
RESTORE file straight from the stored zip, plans them purely, and writes them under the account
owner: pages into `link_archive` (`LinkArchive.entry_for_record` + `record_in`), detections with
the feed's score, firewall and locations, teams, news events, connections, leadership people
(folded into people the run already holds), company facts, notes and the pull log's call table.
Found along the way: the importer took its member list from the passages' ``member`` key, so a
short member merged with its neighbours (location ``from``/``to``) was never offered to the raw
import; it now takes the recorded `document_members` first.

## Phase 5. Screens and exports (4 days)

- Deep dive, one entry per thing in the zip: every uploaded workbook sheet is a tab with all its
  columns (`replica/file_tabs.py` already reads delivered workbooks; extend it to uploaded ones and
  keep hyperlinks as links); every uploaded docx is a document view (sections, tables as tables,
  links live, page by page); every captured page is on `/archive`; every raw pull has a live sheet.
- Files page: the member list from `document_members` with status, counts and a "where it landed"
  link, plus the reconciliation result from `documents.import_report`.
- Notes tab (owner/admin audience) and a Calls sheet (`pull_log_calls` union `tool_calls` with
  parameters, no cost) under `/accounts/[domain]/[sheet]`.
- Sources sheet: add How retrieved and What it supports. News sheet: switch to `news_events`.
  Teams sheet: live from `platform_teams`.
- Findings page: hypotheses, cautions, first-call questions from `report_sections`
  (today only inside the parity Summary tab).
- Render the Evidence prose (Parts D, G, I) in `ParityWorkspace.tsx`.
- Uploaded docx viewer: sections and tables from `document_passages` + the new table units.
- `exports/workspace_files.py`: ship `data/` raw pulls and `data/releases/` (already in the payload
  store) so the product's own zip matches `config/output_spec.yaml`, or drop the "Raw data" promise.

**Status 2026-11-03, night: phase 5 built, green and previewed.** Live deep-dive sheets in
`replica/sheets.py`: `teams` (platform_teams), `connections` (org_connections), `findings`
(every section of the newest Summary, private ones shown here and kept out of the workbook),
`calls` (tool_calls union pull_log_calls, no cost), `notes` (research_notes, private sheet); the
News sheet unions `news_events` (category, why it matters, "From your workspace" chip); the
Sources sheet shows How retrieved and What it supports. `workspace.counts` carries teams,
connections, findings, calls and notes; the account rail lists the five new sheets. The Tech
stack export reads `platform_teams` into the Platform teams sheet (`exports/readers/pg.py`,
snapshot count `platform_teams`). A plainer teams sheet (Team, Platform, Postings in the last 12
months) imports as teams. Web: an owner-only option on the upload form (audience on the start
request), a document viewer at `/accounts/[domain]/documents/[id]` (members with status and
reason, passages grouped by file, tables as text blocks) linked from the uploads card. Previewed
on the local dev app with the synthetic workspace zip (Findings, Platform teams, Notes, Calls,
News, document viewer). Not done: the Evidence prose in `ParityWorkspace.tsx`, raw pulls in the
product's own workspace zip (`exports/workspace_files.py`), and the imported docx as a
page-by-page view (the viewer shows passages and tables as the parser read them).

**Status 2026-11-04: the model reads every upload (owner's rule of 2026-11-03 night).**
Once a document bound to an account is indexed (and, for a workspace zip, imported), the
document service starts the document's own file-mode run by itself
(`app/auto_analysis.py`, key `auto-file:<document id>`, zero credits, an owner-only file starts
nothing, a run limit is logged not raised): the model extracts facts from every passage
(`documents.extract`), the tracker is filled, and a new `summarise` node of the file graph
(`documents/summarise.py`, task kind `file_summarise`) writes the one-minute version,
hypotheses, cautions and first-call questions as private report sections of the run. The account
page now shows findings read from uploaded files (same rule as the deep-dive counts) and the
narrative of a file-import or file-mode run; the Summary's signal tables become `signal_events`
(`signals/events.py::import_events`, typed from their words, scored 0), so the Signals screen and
the account page count every signal the file states. Proofs:
`tests/documents/test_file_runs.py::test_an_upload_bound_to_an_account_is_analysed_by_the_model_by_itself`
and the signal, findings and narrative assertions in `test_workspace_zip_import_db.py`. Cost note
for the owner: every upload bound to an account now makes model calls (extraction per passage
page, one summary call); the credit ledger records them at zero credits like a file analysis.

## Migrations summary

| # | Adds |
|---|---|
| 0102 | `documents.audience` |
| 0103 | `document_members` |
| 0104 | `job_postings.entity_tag`, `summary_read`, `locations text[]`, `merged_count`; `people.note`, `second_profile_url`, `merged_count`; `tech_detections.open_now`, `in_confirmed_stack`, `checked_by_reading` |
| 0105 | `platform_teams` |
| 0106 | `news_events`, `person_comments`, `org_connections`, `research_notes`, `pull_log_calls`; `sources.how_retrieved`, `what_it_supports`, `aliases`; `coverage_rows.needs`, `what_remains`; `tech_detections.score`, `seen_on_postings`, `seen_on_pages`, `behind_firewall`, `locations` |

Each with `.down.sql`, a `versions/` wrapper, an entry in `config/ownership.yaml`, the owner in
`test_single_write_paths.py`, and `EXPECTED_REVISION` bumped in `api/routes/health.py`.

## Tests (per phase, hermetic, no model)

- Phase 0: a zip with `build/jobs_all.json` and the Jobs workbook imports each posting once; the
  70 MB object JSON indexes; the owner-only member is hidden from a second user.
- Phase 1: member rows equal the zip listing; a corrupt member is `failed` and the rest `read`.
- Phase 2: a fixture workspace (the BD zip reduced to 50 rows per sheet, names synthetic) yields
  the exact native rows; a fixture with a repeated requisition, a reposted title, an eight-location
  requisition, two profiles of one person and a page saved twice yields one row each with the
  merged counts in the report, and the raw `document_tables` copy still holds every original row; a rebuilt workbook matches the fixture column for column.
- Phase 3: the Summary fixture yields the expected `report_sections`, `sources` S codes,
  `coverage_rows` and graded facts; the Evidence J extracts attach to the right postings.
- Phase 4: 10 captured pages produce 10 `link_archive` rows with the header date; the detections
  JSON lands with score and pages.
- Gate: `assert_client_safe` on every stored text; the existing leak and name scans unchanged.

## Order and effort

0 (1 d) → 1 (2 d) → 2 (4 d) → 3 (4 d) → 4 (3 d) → 5 (4 d): about 18 working days. Phases 0 and 2
give most of the owner's intent (every row in its table, shown on the page); 3 and 4 give the
docx and archive fidelity; 5 is the screens.

## Open decisions for the owner

1. Should an imported workspace be allowed to reach client files (`may_state_to_prospect`)? Today
   file facts never do unless an admin marks the document authoritative. Proposal: a workspace
   import is authoritative by default when the zip carries `MANIFEST.json` from the product, and
   needs the admin mark otherwise.
2. Grades from the Summary tables: trust them as written (Confirmed stays confirmed) or cap at
   `probable` because the product did not read the source itself? Proposal: trust, flagged
   `imported_grade`, and let the next research refresh re-grade.
3. Keep today's header-matching import for loose workbooks, or require the workspace layout?
   Proposal: keep it, it is what the owner asked for on 2026-10-16.
