# UCB workspace zip: deep audit against the product (upload and research)

Date: 2026-11-05 (evening). Input: `~/Downloads/UCB_workspace.zip` (8,931,084 bytes, built
2026-10-24 by 6th agents, 281 members). The owner's rule: the web account page (the "deep dive"
and its files) must hold exactly what the zip holds after an upload, and a research run must
store its output in the same layout, with no blank files.

## 1. What the zip holds (the reference layout)

| Folder | Members | What |
| --- | --- | --- |
| `RESTORE.md` | 1 | layout, rebuild order, canonical query definitions, zero rule |
| `deliverables/` | 5 | `UCB_Account_Summary.docx`, `UCB_Sources_and_Evidence.docx`, `UCB_People_List.xlsx`, `UCB_Job_Postings.xlsx`, `UCB_Tech_Stack_and_Teams.xlsx` |
| `build/` | 12 | 7 Python and 5 Node scripts that rebuild the deliverables from `data/` |
| `data/` | 25 | raw pulls: `company_record`, `connections` (994), `news_events` (64), `jobs_12mo` (748), `techstack_detections` (201), `techstack_confirmed` (12 functions), `teams` (25), `leadership` (8), `live_board`, `live_postings_index` (97), `live_recapture_log` (41), `org_resolve`, `org_signals`, `subsidiary_domains` (40), `people_grid.txt` (1,730 rows) + `people_grid_meta`, `profiles_full` (1,732), `posts_comments` (241), `people_gapcheck`, `former_exec_checks` (17), `jobs_keyword_counts`, `web_searches` (119), `releases_index` (121), `site_map_news.txt` (206) |
| `data/derived/` | 9 | `postings.json` (767), `people.json` (1,620 + 23 security + 65 brewery + 27 duplicates), `stats.json`, `jobs_classification`, `jextracts` (J1..J16), `pr_jobs`, `pr_stack`, `pr_drops`, `pr_checked`, `captured_urls.txt` (217) |
| `data/releases/` | 224 | one text file per captured page: press releases, web captures, 48 live postings, earnings transcripts, events |
| `notes/` | 5 | `pull_log.md` (every call, counts, output file), `pass1.md`, `pass1_phase2.md`, `sense_check.md`, `README_phase1.md` |

Workbooks and documents (sheet, rows × columns):

- `UCB_People_List.xlsx`: Summary 26×1, People 1,620×17 (`No.`, `Name`, `Title as reported`,
  `Level`, `Meets seniority rule`, `Lane`, `Priority`, `Former SI and consulting employers (firm
  and years)`, `Joined UCB`, `Current role since`, `Location`, `Platform and method
  certifications`, `LinkedIn activity, last 90 days`, `Found by`, `LinkedIn`, `Second profile`,
  `Note`).
- `UCB_Job_Postings.xlsx`: Summary 22×1, Jobs 92×13, Hidden title roles read 12×6.
- `UCB_Tech_Stack_and_Teams.xlsx`: Platform Ranking 23, Confirmed stack 73, Platform teams 28,
  Technology detections 202 (each a one-column title row over a table).
- `UCB_Account_Summary.docx`: 1,223 paragraphs, 7,734 words, 15 tables, 8 H1 sections.
- `UCB_Sources_and_Evidence.docx`: 1,801 paragraphs, 8,236 words, 9 tables, Parts A to I.

Blank files in the zip: none. The smallest member is `people_grid_meta.json` (91 bytes, three
keys). Every `data/releases/*.md` carries a title, link, date and body.

## 2. Upload path: does the account page hold the same thing?

Checked on the current working tree (release `rel-350a4cdd` is this tree) by running the
owner's zip through the real upload path on a scratch database
(`tests/documents/test_exact_copy_real_zip.py`, 3 min 1 s, report in the session scratchpad):

**SAME: the package came back whole.** All 33 sheets and document tables are stored cell for
cell (People 1,620×17, Jobs 92×13, Hidden title roles 12×6, Source register 54×6, …). The five
files are registered as the account's deliverables with the package's own bytes (same SHA-256).
Native rows: people 1,620 = 1,620, postings 104 = 104 (92 Jobs + 12 Hidden title roles).

So the account page's **Files** card lists the five files as uploaded, the People / Job postings
/ Tech screens open the owner's sheets as tabs, and Download everything ships the files byte for
byte with each sheet as CSV (`replica.downloads._fill_imported`). The repair stage does not run.

The member classifier gives every one of the 281 members a role (224 captured pages, 34 raw
pulls, 12 build files, 5 notes, 5 deliverables, 1 restore). The uploads card's "files inside"
list (`GET /v1/documents/{id}/members`) shows all 281 with role and status.

What is still **not** the same as having the zip open on disk:

| # | Gap | Where | Effect |
| --- | --- | --- | --- |
| U1 | A member of the uploaded zip cannot be opened or downloaded by itself. The list names it; the only download is the whole zip under `Internal - your files/`. | `routes/documents.py` (no member endpoint), `ZipMembers.tsx` | "View all the files" shows names, not files |
| U2 | 9 raw pulls are listed but never read: `jobs_12mo`, `people_grid(.txt/_meta)`, `live_board`, `subsidiary_domains`, `people_gapcheck`, `org_signals`, plus everything under `data/derived/` and `releases_index.json`, `site_map_news.txt`, `former_exec_checks.json`, `jobs_keyword_counts.json`, `live_postings_index.json`, `live_recapture_log.json` (no reader). | `workspace_raw.SKIP_PULLS`, `plan()` | They are kept in the zip (so Download everything returns them) but contribute nothing to the deep-dive screens |
| U3 | Staging still holds the import made before file registration; the owner has not re-uploaded. | staging `ucb.com` | Until re-upload, staging's screens show native rows and the download falls back to the zip's deliverables |

U1 is the only one that changes what the owner sees as "the files". U2 is by design (derived
files are rebuilt from the sheets; the feed is used only to give a linkless row its address).

## 3. Research path: does a run store the same layout?

A research run produces two downloads:

1. **Download all** on the delivered-files card = the **workspace zip** (`exports/workspace.py`,
   `exports/workspace_files.py`, config `export_rules.yaml: workspace`). Layout today:

   | Member | Built from | Owner's equivalent |
   | --- | --- | --- |
   | `RESTORE.md`, `MANIFEST.json` | run | `RESTORE.md` (no manifest) |
   | `deliverables/<Account>_{Account_Summary,Sources_and_Evidence}.docx`, `_{People_List,Job_Postings,Tech_Stack_and_Teams}.xlsx` | the five files | same names |
   | `build/{summary,evidence,people,jobs,tech}.json`, `build/urls.json` | render models | `build/*.py|js` scripts (the product never ships builder code, D-25) |
   | `data/entity_lock.json` | account lock | `company_record.json` + `org_resolve.json` + `subsidiary_domains.json` |
   | `data/web_searches.json` | pull calls | `web_searches.json` (119) |
   | `data/techstack_detections.json` | `tech_detections` | same name (201) |
   | `data/releases/*.md` (up to 2,000) | link archive | `data/releases/*.md` (224) |
   | `notes/pull_log.md`, `notes/pass1.md`, `notes/sense_check.md` | pull log, tracker, sense check | same three + `pass1_phase2.md`, `README_phase1.md` |

   **Missing against the owner's `data/`** (the rows exist in the database, nothing is written):
   `news_events.json` (`news_events`), `teams.json` (`platform_teams`), `techstack_confirmed.json`
   (`tech_stack_items`), `jobs_12mo.json` (`job_postings`), `live_postings_index.json` and
   `live_recapture_log.json` (`job_posting_observations`, link archive), `leadership.json`
   (people found on the leadership page), `connections.json` (`org_connections`),
   `profiles_full.json` (`person_profiles`), `posts_comments.json` (`person_posts`,
   `person_comments`), `people_grid.txt` (`people`, `person_search_pages`), `releases_index.json`
   and `site_map_news.txt` (`link_archive`), `company_record.json`, `org_resolve.json`,
   `subsidiary_domains.json` (lock + account), `org_signals.json` (`signal_events`),
   `former_exec_checks.json`, `people_gapcheck.json`, `jobs_keyword_counts.json`; all of
   `data/derived/` (`postings.json`, `people.json`, `stats.json`, `jobs_classification.json`,
   `jextracts.json`, `pr_*.json`, `captured_urls.txt`).

   Of these, `connections.json`, `profiles_full.json` and `posts_comments.json` are provider
   payloads. Decision D-25 (ADR-0028) excludes raw broker payloads from the zip until the broker
   terms (D-10) are settled, and organisation-provider payloads are never shipped. The owner's
   zip ships them. **This is the owner's decision to take**; the product cannot silently reverse
   D-25.

   Blank-file risk inside this zip: `data/entity_lock.json` becomes `{"recorded": false, "note":
   …}`, `notes/sense_check.md` one line, `notes/pass1.md` one line when the run recorded nothing.
   They are not empty, but they carry no research. The zip is built only when all five files
   passed (B13): a run with a withheld file (Olympus 2026-11-04: 2 of 5) has **no** workspace zip
   at all, and the Download all button is not drawn.

2. **Download everything** (`replica/downloads.py`) = `Deliverables/`, `Tables/` (repair-stage
   tables as CSV + XLSX), `Logs/`, `Internal - your files/`, `README.txt`. This is the
   product's own layout, not the owner's. A table with zero rows is written as a header-only
   CSV and an empty workbook (athenahealth 2026-10-23: `News.csv` 43 bytes, `Signals.csv` 45
   bytes, `From your mail and drive.csv` 66 bytes). **These are the blank files.**

3. The deep-dive screens (Signals, People, Job postings, Tech, News, Sources, Archive, Web
   searches) read the native rows; a research run has no "files inside" list like an upload.

## 4. Findings, ranked

| # | Finding | Severity |
| --- | --- | --- |
| R1 | The research workspace zip holds 3 of the owner's 25 `data/` files and none of `data/derived/`; the rows for 17 of them are already in the database. | High: the owner's "same format" rule |
| R2 | Download everything writes header-only CSVs and empty workbooks for tables with no rows. | High: "no blank files" |
| R3 | The owner's `notes/` has 5 files; the product writes 3. `README_phase1.md` (package description) and `pass1_phase2.md` (phase-2 additions) have no equivalent. | Medium |
| U1 | A member of an uploaded zip cannot be opened on its own from the web. | Medium: "view all the files" |
| R4 | A run with one withheld file has no workspace zip at all (B13). | Medium: files missing, not blank |
| R5 | `entity_lock.json`, `sense_check.md`, `pass1.md` are one-line placeholders when the run recorded nothing. | Low: upstream (the run should always record them) |
| D1 | Provider payloads (`connections`, `profiles_full`, `posts_comments`) and build scripts are excluded by decision D-25. | Owner's decision |

## 5. Plan

Build, in this order, each with its own test:

1. **R2**: a table with no rows is not written; the README names it under "Tables with no rows
   this run". The repair report keeps the count. (`exports/repair/package.py`, test in
   `tests/exports/repair/`.)
2. **R1**: `data/` members built from stored rows in the owner's file names and record shapes,
   flat JSON records with the owner's field names (the importer reads flat and JSON:API alike):
   `company_record.json`, `org_resolve.json`, `subsidiary_domains.json` (from the lock and the
   account), `news_events.json`, `teams.json`, `techstack_confirmed.json`, `jobs_12mo.json`,
   `live_postings_index.json`, `leadership.json`, `org_signals.json`, `releases_index.json`,
   `site_map_news.txt`, `people_grid.txt`; `data/derived/postings.json`, `people.json`,
   `stats.json`, `captured_urls.txt`. Each from `SnapshotContent` rows the reader already loads
   or loads anew; a source with no rows writes the one-line note, never an invented row; added
   to `required_members` and `RESTORE.md`'s layout. Provider payload members stay out (D1).
3. **R3**: `notes/README.md` (what the package is: account, run date, pursuing client, contents)
   and the phase-2 additions appended to `pass1.md` as a second section.
4. **U1**: `GET /v1/documents/{id}/members/{index}` streams one member out of the stored zip (the
   same scan rules as the whole file); the files-inside list links each member.
5. **R4**: the workspace zip is built from the files that passed, the withheld ones named in
   `RESTORE.md` and the manifest (not instead of withholding: the defective file stays out).

Not in this plan: shipping provider payloads or build scripts (D1), changing the repair stage,
any paid run. The research-side checks use the stored rows of past runs and the synthetic corpus.

## 6. Status (2026-11-05, night): built, uncommitted, not deployed

| # | Built | Where | Proof |
| --- | --- | --- | --- |
| R2 | A table or sheet with no rows gets no file; the README names it (`NO_ROWS_NOTE`, `EMPTY_SHEETS_NOTE`) | `exports/repair/package.py` | `tests/exports/repair/test_package.py::test_tables_and_sheets_without_rows_get_no_file_and_a_readme_line` |
| R1 | 14 new `data/` members in the owner's names and record shapes, from stored rows only: `company_record`, `subsidiary_domains`, `news_events` (new `NewsRow` on the snapshot, read from `news_events`), `jobs_12mo`, `live_postings_index`, `techstack_confirmed`, `teams`, `people_grid.txt`, `releases_index`, `site_map_news.txt`, `derived/postings`, `derived/people`, `derived/stats`, `derived/captured_urls.txt`; a source with no rows prints the one-line note | `exports/workspace_files.py`, `exports/content.py`, `exports/readers/pg.py`, `exports/config.py` (`DataFileRules`, `ReadmeRules`), `config/export_rules.yaml` (`required_members`, `data_files`, `readme`) | `tests/exports/test_workspace_layout.py::test_data_members_hold_the_stored_rows_in_the_owners_shapes`; the whole `tests/exports` suite |
| R3 | `notes/README.md` (what the package is, folder by folder) | same | same |
| U1 | `GET /v1/documents/{id}/members/{member_id}` serves one file of an uploaded zip as uploaded (cap 100 MB, audit `download_document_member`, a missing blob is 404 not 500); the member list carries `id`; the uploads card's "files inside" list gets "Download this file" per member | `documents/service.py::member_file`, `documents/repo.py`, `api/routes/documents.py`, `api/policy.py` (route catalog entry), `api/contract.py`, `web/components/accounts/ZipMembers.tsx`, `tests/isolation/matrix/matrix.yaml`, OpenAPI + web client regenerated | `tests/documents/test_workspace_zip_import_db.py` (member bytes = the zip's); isolation matrix 568 passed; route catalog tests; `tsc` and the web unit tests; in the local preview the link renders for all 19 members and reaches the service (the local blob store had lost the upload, so 404) |
| R4 | Already so: `builder._zip` builds the zip from the files that passed and names the withheld ones (`build_workspace(withheld=…)`); only a zip that itself fails the client-output scan is left out. The module docstring was corrected. | `exports/builder.py` | existing |

D1 decided by the owner (2026-11-05 night): provider payloads and build scripts stay out of the
research zip (D-25 stands). Still open: U3 (re-upload the UCB zip on staging), `pass1_phase2.md`
(no stored source), and the deploy.
