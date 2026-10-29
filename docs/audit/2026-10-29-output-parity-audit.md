# Output parity audit: the product's files against the owner's reference set (2026-10-29)

*Nine audits ran in parallel on the night of 2026-10-29 against the owner's reference deliverable
set (an account research finished by the owner's Claude.ai research skill on 2026-10-20: three
workbooks, three Word documents, the phase 1 zip and the workspace zip). This page records what
was found, what was fixed the same night, and what is left for the owner. The reference account is
never named in code, config, fixtures or tests (owner rule of 2026-10-27); here it is "the
reference". Full evidence with file:line pointers sits in the session scratchpad reports
(`audit_jobs`, `audit_people`, `audit_tech`, `audit_jobs_pipeline`, `audit_web`, `audit_docx`,
`audit_tests`, `audit_classify`, `port_archive`); the measured shape of the reference files is
summarised in section 2 below so the repository keeps the spec.*

## 1. The owner's requirements (plain words)

1. Every workspace's files must be a copy of the reference shape: the same sheets in the same
   order, the same column names in the same order, the same row rules (which postings and people
   are rows, how they are classed), the same Summary sheets with live formulas, the same look.
2. Every link in every output must be a working hyperlink, and beside it a second link that opens
   our archived copy of the page, because closed postings vanish from careers sites (404) and
   profiles move. Archived copies are stored as JSON/text records.
3. A re-research of an account must not re-pull and re-pay the whole 12 months: it pulls only what
   is new since the last research, keeps every earlier row, re-checks open/closed, and flags the
   new rows so the seller sees only what changed.
4. The web screens show the same tables as the Excel files, sheet by sheet, with the links.
5. Everything hardened and tested; nothing deleted; nothing committed until the owner says so.

## 2. The reference shape (measured)

### Job Postings workbook: Summary, Jobs
Jobs sheet, 14 columns in this order, freeze `C2`, filter over the table, rows sorted by first seen
descending: Requisition | Title | Platforms named | Class | Status | First seen | Last seen |
Location | Seniority | Entity | What the posting says (read) | Platform sentences | Full
description | Link. `Status` is `Open` or `Closed`. `Class` is one of `Build role`, `Admin and
owner role`, `Working tool`, `User role`. `Link` shows the text `Open posting` with the posting URL
behind it. `Platform sentences` reads `Salesforce: s1 | s2 || SAP: s3` (up to four sentences per
platform). `Entity` is the account's short name, or a tagged divested business or subsidiary.
Summary sheet: title line, five note paragraphs, a navy header row `Platform (named anywhere),
<Account> positions only | Total | Closed | Open | Build role | Admin and owner role | Working tool
| User role`, seven platform rows (Salesforce, MuleSoft, ServiceNow, SAP, Tableau, Veeva, Workday)
of `COUNTIFS` over the Jobs sheet with an entity prefix criterion, then three row-count lines.

### People List workbook: Summary, People
People sheet, 13 columns, freeze `C2`: No. | Name | Title as reported | Level | Meets seniority
rule | Lane | Priority | Former SI and consulting employers (firm and years) | Found by | LinkedIn |
Second profile | Location | Note. Nine level labels, eight lane labels, Priority A/B/C with cell
fills, `LinkedIn` shows `Open profile` with the profile URL behind it. Summary: title, six notes,
a Level table (nine levels, People and Priority A counts, Total, Meets seniority rule), a Lane
table (eight lanes, A/B/C/Total for senior people only).

### Tech Stack and Teams workbook: Platform Ranking, Confirmed stack, Platform teams
Platform Ranking: title, base count in F2, four notes, header at row 8 (Rank | Platform | Category
| Lane | Postings naming it | Share of base | Open now | Last seen | In confirmed stack | Matches
checked by reading), rank as a formula (A 15%/5, B 5%/3, C 2), a Counts block, a Seen once block.
Confirmed stack: Business function | Category | Technology | Postings naming it | Lane, from the
organisation tech stack pull. Platform teams: Team | Lane technologies named | Postings count |
First activity | Last activity | Team technologies (name, postings) | Team locations (place,
postings) | Job functions.

### Common look
Arial 10; header rows bold white on navy `1F3A5F`; body borders `BFC5CC`; hyperlinks blue
`1155CC` underlined; titles Arial 14 bold navy; notes merged across the table and wrapped.

### Word documents
Letter landscape; Arial; real heading styles with a page break before each level 1 heading; grade
and status cells filled by value; 156 hyperlinks in the Summary (Key people profiles, source
list) and 278 in the Evidence file (every source and posting row); footer with the page number
only.

### Zip
`RESTORE.md`, `deliverables/`, `data/` (raw pulls as JSON plus one text file per page read under
`data/releases/`), `build/` (the scripts), `notes/` (pull log, pass 1, sense check).

## 3. What the audit found (before any fix)

### Job Postings workbook and pipeline
| # | Finding | Severity |
| --- | --- | --- |
| J1 | `Link` is the raw URL as plain text: no hyperlink, no `Open posting` label. No test noticed. | high |
| J2 | `Last seen` and `Seniority` are dropped at the first hop into the job pipeline (`jobs/adapters.py`); the feed record carries both. | high |
| J3 | Feed status `null` becomes `unknown`; only live-board hits print `Open`. The closed label is the sentence "Open or closed in the last 12 months". The Summary matrix counts those words, so its Open/Closed columns are wrong. | high |
| J4 | The Summary matrix has no entity criterion, lists Microsoft Dynamics (always 0, wrong dictionary name), orders Workday before Veeva, adds an All rows line and seven class columns. | high |
| J5 | Entity is decided by URL host only; the reference tags a divested business and a subsidiary by text patterns and a close date. No per-account rule exists. | high |
| J6 | `Platform sentences` joins every sentence with newlines, no platform prefix, no cap. | medium |
| J7 | `First seen` is "posted date else first seen" as a date value; rows sorted ascending. | medium |
| J8 | Three product-only columns (Found by, Ref, Source code) and two product-only sheets (Counts, Long text). Ref is load-bearing for row conservation and the COUNTA formulas. | medium |
| J9 | The feed pull sends only `first_seen_at_from` at page size 100; with caps on it stops at 20 pages / 2,000 postings and marks the pull partial. The reference pulled 7,148 postings over eight pages of 1,000. | high |
| J10 | A second research of the same account re-pulls the whole year; nothing reads a previous run's postings; the research cache replays feed pages free only within 3 days and the same calendar month. | high |
| J11 | Live-board-only postings print a blank First seen instead of the capture date. | low |

### People List workbook and rules
| # | Finding | Severity |
| --- | --- | --- |
| P1 | The product would list 371 of the reference's 736 people: 277 titles without a level cue are never listed (the reference defaults them to "Lead, owner, specialist or developer"), 88 Manager/Sr. Manager titles are out of scope; below-rule people whose title names a platform are dropped, so "Meets seniority rule" is always Yes. | high |
| P2 | Level agrees on 280 of 736 titles: no Senior Director or Associate Director level, "Sr. Manager" not read, CIO/CTO/Dir/Mgr unknown. | high |
| P3 | Lane agrees on 78 of 736: the reference assigns one lane by role-family words; the product matches platform names only, allows several lanes, and has six labels instead of eight. | high |
| P4 | Priority agrees on 207 of 371 rated rows: the reference rates on the title alone. | high |
| P5 | "Former SI and consulting employers" is always "Not pulled" (profile port unavailable in production; no dated employments; no SI firm list). | medium |
| P6 | "Found by" prints three fixed labels; the reference prints the search source with pass and lane. | medium |
| P7 | Exclusions: `cyber`, `cybersecurity`, `internal audit`, `privacy officer` not covered; `config/skill/scope.yaml people_exclusions` is loaded and used nowhere. | medium |
| P8 | Look: Calibri on light blue, no Priority fills, widths by kind, two extra columns; Summary one row off (five notes instead of six), formulas cite literal labels. | medium |

### Tech Stack and Teams workbook
| # | Finding | Severity |
| --- | --- | --- |
| T1 | The second sheet is the wrong data: "Stack detections" (technology detections feed) instead of the reference's "Confirmed stack" (organisation tech stack pull). The stack pull is already fetched every run and never stored. | high |
| T2 | Teams are not implemented anywhere (no provider spec, no table, no pipeline); the sheet is a title and "None recorded". | high (owner item) |
| T3 | Ranking header says "In stack detections"; lane labels differ on every laned row; SAP is matched case-insensitively ("a sap of", "concur with you" count); six other patterns differ. | medium |
| T4 | Open now counts `status == open` (reference: not closed); Last seen is max(posted else first seen) (reference: max last seen). | medium |
| T5 | Rank fills, bold rank cells, `0.0%`, per-column widths and note heights missing. | low |

### Web tables
| # | Finding | Severity |
| --- | --- | --- |
| W1 | The Jobs table of a real account is blocked outright: the client-output guard raises 422 for the whole page when a description contains the ordinary word "parallel" (a provider display name matched case-insensitively). The page shows "Table not found"; every sort/filter/Show more link fails. This explains the owner's "some buttons not working". | critical |
| W2 | The Jobs Link column is dead text on screen (same cause as J1). | high |
| W3 | After any column is added, every earlier delivery falls back to the live research sheet with other columns and no tabs (exact header-list equality). | high |
| W4 | No character cap on the main-table page: 2 MB for 200 rows, 4.4 MB for 500, and the guard scan takes 3 to 8 s against a 10 s client timeout. | high |
| W5 | Cell fills and row highlights cannot be shown (no fill token in the row models; CSP forbids inline styles). | medium |
| W6 | Unstyled-header heuristic bug on files without the builder's header style; the formula evaluator shows blank for unsupported functions even when the file has a cached value. | low |

### Word documents
| # | Finding | Severity |
| --- | --- | --- |
| D1 | No hyperlinks at all: URLs are printed as text; Part E prints no URL. | high |
| D2 | Portrait Letter with 1 in margins (reference landscape, 0.9 in). | medium |
| D3 | Headings are plain paragraphs with heading fonts (no navigation, no page breaks). | medium |
| D4 | No grade or status fills; light blue header instead of navy; Calibri. | medium |
| D5 | Every table starts with a Ref column the reference lacks (load-bearing for row checks). | owner decision |
| D6 | Section and part headings match the reference word for word; column sets differ in Key people, Findings from profiles, Part E, Part H, Part I. | medium |

### Tests and operations
| # | Finding | Severity |
| --- | --- | --- |
| Q1 | Workbook recalculation never runs on staging: `EXPORT_RENDER_CHECKS` is not set in Terraform, and the images install `libreoffice-writer` only (no Calc). | high |
| Q2 | Column parity is proven only against the config, never against a reference manifest; 17 test sites pin literal headers. | medium |
| Q3 | No incremental re-research exists and nothing tests one. | high |
| Q4 | Long-text continuation is proven at a 400-character test limit only; People and Tech books have no Long text sheet, so an over-limit cell fails the build. | medium |
| Q5 | 1,077 tests passed across exports, jobs, people and replica before any change (464 s for exports alone). | info |

### Link archive
The link archive (table `link_archive`, writers for every stored page, posting, profile and
detection, a tenant-scoped route that serves the newest copy) had been built on 2026-10-27 in a
side worktree and never reached the main tree. It was ported tonight (34 files, migration 0086),
leaving out the account-delete and parity-bundle work of that worktree.

## 4. What was changed tonight

Each part has its own done note beside this page (`2026-10-29-jobs-workbook-done.md`,
`-people-workbook-done.md`, `-tech-workbook-done.md`, `-web-tables-done.md`).

**Look (all three workbooks and both Word files).** The theme is now the reference's: Arial, navy
`1F3A5F` header with white bold text, `BFC5CC` borders, blue underlined links, named cell fills
(Priority A/B/C, Rank A/B/C, a soft fill for new rows), per-column widths and wrap, header heights.
One theme file (`config/export_theme.yaml`) is the only place a style value lives.

**Job Postings.** The 14 reference columns in the reference order and widths, then `Archived link`
("Open archived copy"), `New since last research`, and the product's `Ref` and `Source code` on
the right. `Link` is a real hyperlink with the text "Open posting". Rows sort by first seen,
newest first. Platforms follow the fixed order, sentences read `Platform: s1 | s2 || Platform:
s3`, status words are Open and Closed, First seen and Last seen come from the feed, Seniority is the
feed word. The Summary sheet is the reference's: five notes, the seven platforms, COUNTIFS with an
entity prefix criterion, three row-count lines. Entity rules are data (patterns plus an optional
date) applied before the host rule. Workday, Concur, Ariba, eTMF and CTMS now count; the Apex alias
(false Salesforce hits) is gone; SAP matches case-sensitively. Working tool / User role come from
the reference's sentence heuristic when no reader decided; the keyword rules only triage.

**People List.** All 736 reference titles now qualify (371 before). Measured on those titles:
Level agrees on 734, Lane on 734 (was 78), Priority on 718 (was 207 of 371 rated). Below-rule people
whose title names a platform stay listed with "No". Eight lanes, one per person. Exclusions add
cyber, cybersecurity, ciso, internal audit and privacy officer. Found by prints the search source
with pass and lane. Former SI employers use a 28-firm list with dated employments (empty without
profile data). The Summary has six notes and the Level and Lane tables at the reference rows.

**Tech Stack and Teams.** The organisation tech stack is now stored (table `tech_stack_items`,
migration 0090) and drives the second sheet, `Confirmed stack`, with Business function. Platform
Ranking follows the reference: "In confirmed stack", lane words, SAP case-sensitive, the other
pattern differences, Open now and Last seen rules, rank fills. Platform teams has the reference
layout and shows "None recorded" until a teams provider operation exists.

**Incremental re-research.** At run creation the account's previous delivered run is found and the
run input carries `postings_previous_run` and `postings_since` (previous research date minus seven
days). The feed call and the pipeline read the bound from one place, so the pull is paid once. The
previous run's postings are carried forward with no read and no model call, only the delta is
pulled and classified, one extra active-postings call refreshes open/closed, rows first seen
since the previous research are flagged (`new_since_previous`, shown as a column and a row fill),
and nothing is deleted. Fields: migration 0087.

**Link archive and original/archived links.** The archive built on 2026-10-27 is in the main tree
(migration 0086). Every posting, profile, page and detection a research stores is indexed; the
route `GET /v1/accounts/{domain}/archive?url=` serves the newest copy; the Jobs workbook, the
Word source register and posting cells carry "Open archived copy" beside the original link.

**Web tables.** The Jobs table no longer fails on the word "parallel" (the provider display name is
matched only as the vendor), a guarded cell is withheld instead of the whole page failing, a
delivered account never falls back to the live sheet, link columns open, fills show, cells are
capped (page 2.08 MB to 0.60 MB for 200 rows; guard 3 s to 0.4 s), formulas fall back to the
file's cached value, filter chips and downloads work on file-shaped tabs.

**Word documents.** Real hyperlinks (Key people profiles, source list, every evidence row),
landscape pages, heading styles with page breaks, grade and status fills, navy table headers, a
page-number footer, the "what changed" line, and the white-label brand header kept.

**Operations.** The worker and API images install `libreoffice-calc` and Terraform sets
`EXPORT_RENDER_CHECKS=on`, so workbook formulas are recalculated and compared with the code values
on every delivery (this had never run on staging). Tenant deletion now also erases the link archive
and the tech stack (migration 0091).

## 5. Owner decisions and open items

1. The `Ref` and `Source code` columns: the reference has 14 Jobs columns; the product's row
   conservation check and the COUNTA formulas key on `Ref`. Tonight's build keeps `Ref` and
   `Source code` to the right of the reference columns (after `Archived link`). Decide whether
   they should be hidden columns or dropped (then the row key moves into a hidden column).
2. The `Long text` sheet: the reference caps Full description at 32,000 characters; the product
   continues long text on a sheet so nothing is cut. Keep the sheet or cap.
3. Platform teams: the provider operation has no specification yet (no table, no pipeline). The
   sheet renders the reference layout with a "None recorded" line until the operation is specified.
4. Former SI and consulting employers: needs a profile pull in production (today unavailable) and
   dated employments.
5. Per-account entity rules (a divested business, a subsidiary): now data on the account (entity
   lock), applied in code; the owner supplies the labels and patterns per account.
6. People title exclusions: bare `audit` and `compliance` go to review today; the reference
   excluded every `audit` title. Decide the rule.
7. Workbook recalculation on staging: set `EXPORT_RENDER_CHECKS=on` and add `libreoffice-calc`
   to the worker image.
