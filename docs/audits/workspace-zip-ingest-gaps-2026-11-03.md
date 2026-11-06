# What the product keeps when a workspace zip is uploaded, and what it loses (2026-11-03)

Question from the owner: when a research-workspace zip (the BD_workspace.zip reference) is uploaded,
the product should open every file and page, read everything, store it in the file store and the
database in its proper shape, and show it. This audit compares that intent with the backend, the
schema and the web screens as they are on disk today (uncommitted changes included). Companion:
`bd-workspace-zip-audit-2026-11-03.md` (what is in the zip).

## 1. What happens today, step by step (verified in code)

1. `POST /v1/documents` accepts `zip` (`config/research_input_modes.yaml`, `upload.document_types`),
   up to 20 GiB. The browser uploads straight to object storage; the file is scanned and moved to
   `tenants/<t>/raw/<sha256>`. **Only the original zip is stored as a blob; members never get their
   own blob or their own `documents` row.** The whole zip is one `documents` row, cut into
   `document_parts` of about 64 MiB of declared member size.
2. `_index` runs **only when the document is bound to an account**. An unbound zip is parsed but
   never embedded or imported.
3. Every member is streamed through the parser for its type (`research_files/parsers/archives.py`,
   `max_members` 2000, `max_depth` 2, ratio 200). Nothing is filtered by extension; `.py` and `.js`
   are read as plain text. The member path is kept as `location.member`.
4. Per type:
   - **docx** (`parsers/documents.py`): one `section` passage per heading; **tables are flattened
     to `a | b | c` text lines**; **hyperlink targets are dropped** (only the visible text survives);
     page breaks and styles are lost.
   - **xlsx** (`parsers/tabular.py`, `load_workbook(read_only=True, data_only=True)`): every sheet
     becomes a table in `document_tables` + `document_table_rows` plus one `crm_row` passage per row;
     **formulas are lost (cached values only)**; a hyperlink survives only when the cell text is a
     label such as "Open profile" / "Open posting" (a link on a Name cell is dropped).
   - **md / txt / py / js**: prose passages; `title:` / `url:` / `date:` header lines are plain text.
   - **json**: records become table rows; one nesting level is flattened, deeper objects become
     JSON text, lists are cut to 2,000 characters.
5. Prose is chunked (400 to 800 tokens) and embedded into `chunks`; table rows become `crm_row`
   passages.
6. `documents/deliverables.py` then matches **every crm_row table by its column names** (Jobs,
   People, Confirmed stack / Platform Ranking, Platform teams) and writes **one `facts` row per
   data row** (`product_grade='internal'`, `visibility='private'`, flags `workspace_import`) with
   one `fact_evidence` row (a quote of at most 600 characters). **Nothing is written to
   `job_postings`, `people`, `tech_detections`, `tech_stack_items`, `sources`, `link_archive`,
   `report_sections`, `coverage_rows` or `signal_events`.** The account page and deep-dive sheets
   union these facts into their rows; they never reach a client file.

## 2. Per member of the reference zip: kept, where, lost

| Zip member | Kept as | Stored in | Lost today |
|---|---|---|---|
| `deliverables/*_Account_Summary.docx` (34 pp, 14 tables, S1..S68, J1..J52) | heading sections + flattened table lines | `document_passages`, `chunks` | table structure, grades per row, source register, J IDs, news table, signals tiers, tracker, hypotheses, every hyperlink URL. None of it reaches `report_sections`, `sources`, `facts` or `coverage_rows`; reachable only through Ask search |
| `deliverables/*_Sources_and_Evidence.docx` (60 pp; 147 detections, 123 news events, 92 live postings, 52 extracts) | same | same | all of Part A..I as structured data; URLs in Part I survive only as text |
| `deliverables/*_People_List.xlsx` People sheet (736 x 13) | table + crm_rows; facts `people.full_profiles_senior_people.<slug>` | `document_tables`, `document_table_rows`, `facts`, `fact_evidence` | **Former SI and consulting employers** (not mapped); `people` / `person_profiles` rows (not written); same-name rows collapse to one fact (fingerprint) |
| People Summary sheet (counts, exclusion list) | table rows only | `document_tables` | no fact, no excluded-people record |
| `deliverables/*_Job_Postings.xlsx` Jobs sheet (1,897 x 14) | table + facts `jobs.job_postings_read_classified.<slug>` | same | **What the posting says, Platform sentences, Full description** (only inside the 600-char quote); `job_postings` / `job_posting_platform_sentences` / `job_posting_classifications` rows (not written); the 1,041 rows with a blank Requisition collapse by title |
| Jobs Summary sheet (COUNTIFS) | cached numbers as table rows | `document_tables` | formulas |
| `deliverables/*_Tech_Stack_and_Teams.xlsx` Confirmed stack (212) | table + facts `tech.technology_detections.<slug>` | same | `tech_stack_items` rows (not written) |
| Platform Ranking | facts (rank, sources, open_now, last_seen) | same | rank and share formulas; "In confirmed stack", "Matches checked by reading", the Seen-once list |
| Platform teams (25) | facts with `category='team'` (function, sources, lanes, technologies, locations) | same | **First / Last activity**; there is no teams table at all |
| `BD_Owner_Only.docx` (owner-only) | prose passages like any member | `document_passages`, `chunks` | **nothing marks it owner-only**; it is searchable by every user of the account |
| `notes/pull_log_phase1.md`, `pass1.md`, `sense_check.md`, `RESTORE.md` | prose passages | same | the pull-log table (tool, parameters, counts, output file) as rows; no notes table; no screen |
| `data/releases/*.md` (302 captured pages with `title:/url:/date:/source:`) | prose passages | same | **no `link_archive` row, no `sources` row, no news fact, no `signal_events`**; url and date are plain text; the /archive screen never shows them |
| `data/jobs_12mo.json` (50 MB, top-level object with `jobs: [...]`) | JSON table rows (7,148) | `document_tables`, `document_table_rows`; plus facts if the column names match | **risk of failing the part**: an object-shaped JSON is loaded whole (`json_max_document_bytes` 64 MiB) in a 1,536 MB sandbox and emitted in 5,000-row units against a 64 MiB line cap; a failure marks the whole zip `failed / parts_failed`, so nothing is embedded or imported |
| `data/profiles_full.json` (11 MB), `posts_comments.json` (10 MB) | JSON rows | same | nested `profile` / `posts` / `comments` objects become JSON text; lists cut at 2,000 chars; no `person_profiles`, `person_posts` rows; comments have no table anywhere |
| `data/techstack_detections.json` (1,022), `techstack_confirmed.json`, `teams.json`, `news_events.json` (123), `connections.json` (1,000), `org_signals.json`, `leadership.json`, `company_record.json`, `org_resolve.json`, searches | JSON rows | same | no provider-shaped importer: nothing reaches `tech_detections`, `tech_stack_items`, `signal_events`, `sources` or a news table; `teams.json.credits_used` is stored as data |
| `data/people_grid.txt`, `site_map_news.txt` | prose | same | the grid is a 1,484-row TSV read as text, not a table |
| `build/*.json` (`jobs_all.json` 48 MB, `jobs_bd.json` 43 MB, `jobs_lane_rows_final.json` 14 MB, `people_merged.json`, ...) | JSON rows; **also matched by the deliverables importer** when column names fit | same | duplicates of the workbook facts (jobs three times over); same part-failure risk as above |
| `build/*.py`, `*.js` | plain text passages | same | nothing useful; should be skipped |

## 3. Missing tables

| Needed for | Today | Proposed |
|---|---|---|
| Zip members as first-class files | one `documents` row for the whole zip | `document_members` (document_id, member_path, kind: deliverable/note/captured_page/raw_pull/build/other, declared role, sha256, bytes, status) or one `documents` row per member with a `parent_document_id` |
| Platform teams | no table (`exports/content.py`: "No pipeline stores teams yet") | `platform_teams` (team, function, postings_count, first_activity, last_activity, lane_technologies text[], technologies jsonb, locations jsonb, source_ref) |
| News feed events (123 per account) | facts `news.%` only | `news_events` (event_id, category, headline, summary, found_at, effective_date, confidence, source_url, location, related jsonb) |
| Captured pages from an upload | `link_archive` written only by the research graph | an upload writer for `link_archive` (kind page/posting/feed_item) keyed by the `url:` header, plus the copy in the payload store |
| Source register from an upload | `sources` written only by the graph | an upload writer that reads the register table (S id, source, type, date, how retrieved, what it supports) |
| Analyst notes, decisions, pull log | none | `research_notes` (account_id, run_id, kind: pull_log/pass1/sense_check/restore, text) and `pull_log_calls` (step, tool_role, parameters, result_count, output_file) |
| Profile connections and comments | none (`person_posts` holds posts only) | `person_comments`; `org_connections` (category, company1, company2, first/last seen, source_url) |
| Owner-only material | none | `documents.audience` (client / owner_only / internal) honoured by Ask and the sheets |

## 4. Missing columns on existing tables

| Table | Missing | Note |
|---|---|---|
| `job_postings` | `entity_tag` (BD / divested business / pre-close divested / subsidiary), `what_the_posting_says` (authored summary), `read_in_full` | entity is recomputed at export; "what" exists only as the classification sentence |
| `people` | `note` (authored), `former_si_employers` (or keep deriving from `person_profiles.employments`), `second_profile_url` as a stored merge result | the legacy export hard-codes `note=""` |
| `tech_detections` | `score`, `seen_on_postings`, `seen_on_pages`, `behind_firewall`, `locations` | the detections feed carries all five |
| `tech_stack_items` | `in_confirmed_stack` cross-reference, `matches_checked_by_reading` | ranking columns |
| `sources` | `how_retrieved`, `what_it_supports` (authored), `read_depth` | derived from `fact_evidence.origin` today |
| `coverage_rows` | `needs`, `what_remains` (authored) | label maps at export |
| `facts` (news) | `why_it_matters` (authored) | "the platform stores no authored reason per finding" (`exports/readers/summary.py`) |
| `documents` | `audience`, `member_count`, `member_failures` | see section 3 |
| `report_sections` | kinds for `caution` and `how_this_could_be_wrong` per account (today fixed config text) | |

## 5. Missing pages (web)

| Reference content | Screen today | Gap |
|---|---|---|
| Notes: pull log, pass 1, sense check, RESTORE | none | a Notes tab under the account, read-only, owner/admin audience |
| Pull log of paid calls with parameters | none for clients (searches sheet shows web searches only) | a Calls sheet over `tool_calls` (tool role, parameters, count, cost hidden) |
| Evidence Parts D, G, I (extracts, keeping copies, full URLs) | in the bundle prose, not rendered (`ParityWorkspace.tsx` has no Evidence prose component) | render the Evidence prose |
| Source register with How retrieved / What it supports | sources sheet shows Code, Title, Publisher, Published, Link | add the two columns |
| Platform teams with activity dates | file tab only (from the delivered workbook) | a live Teams sheet once the table exists |
| News feed events (date, category, headline, source) | news sheet shows facts only | a live News feed sheet |
| Hypotheses, cautions, first-conversation questions | only inside the parity Summary tab after a bundle is built | a standalone Findings page from `report_sections` |
| Uploaded docx deliverables (Summary, Evidence) | nothing shows them as documents; only Ask search | a document viewer tab for uploaded docx (sections + tables), like the workbook file tabs |
| Zip member inventory (what was read, skipped, failed) | coverage text in Ask only | a Files page row per member with status |
| Owner-only file | indistinguishable from the rest | hidden from non-owners |

## 6. Missing files in the product's own workspace zip (vs the reference)

The product's zip (`exports/workspace_files.py`) ships RESTORE.md, MANIFEST.json, the 5 deliverables,
`data/entity_lock.json`, `data/web_searches.json`, `data/techstack_detections.json`, `build/<kind>.json`,
`build/urls.json` and three notes. Missing against the reference: `data/jobs_12mo.json` (all postings),
`profiles_full.json`, `posts_comments.json`, `teams.json`, `news_events.json`, `connections.json`,
`techstack_confirmed.json`, `org_signals.json`, `leadership.json`, `company_record.json`,
`org_resolve.json`, the person and company search files, `data/releases/` (captured pages) and the
`build/` scripts (by design; `config/output_spec.yaml` still promises "Build scripts" and "Raw data").

## 7. Risks to fix first

1. **The 50 MB object-shaped JSON can fail its part and take the whole zip with it** (`failed /
   parts_failed`: no embedding, no import, dropped from `list_sources`). Stream `jobs:[...]` inside an
   object, or raise the unit cap, or split members into their own documents so one failure is local.
2. **Duplicate imports from `build/*.json`**: the deliverables importer matches any crm_row table with
   the right headers, so `jobs_all.json`, `jobs_bd.json`, `jobs_lane_rows_final.json` and
   `people_merged.json` each add the same rows again. Route by member role instead of by headers.
3. **Owner-only docx becomes searchable** by every user of the account.
4. **Hyperlinks dropped** on docx and on xlsx cells whose text is not a label; the product's own
   People sheet uses "Open profile" (kept) but a future export with the URL as text would lose it.
5. **Formula cells**: the reference workbooks carry cached values; a workbook written by openpyxl
   without cached values imports as blanks for every formula cell (`data_only=True`).
6. `query_table` refuses an unfiltered query over more than 5,000 rows, so Ask cannot count the
   7,148-row jobs table without a filter.

## 8. The shape that would meet the intent

A `workspace import` mode, chosen when the zip holds `RESTORE.md` or `MANIFEST.json`:
`deliverables/` workbooks into the native tables (`job_postings` with sentences and classes, `people`
+ `person_profiles`, `tech_stack_items`, `platform_teams`), the two docx files into `report_sections`,
`sources`, `facts` with grades and `coverage_rows` by reading their tables as tables, `data/releases/`
into `link_archive` with the `url:/date:` header, `data/*.json` through provider-shaped importers
(`tech_detections`, `news_events`, `person_posts`, `org_connections`), `notes/` into `research_notes`
and `pull_log_calls`, `build/` scripts skipped, the owner-only file marked `audience=owner_only`,
and one `document_members` row per member so the Files page can show what was read, skipped or failed.
