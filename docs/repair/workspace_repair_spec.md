# Workspace repair: the "Download everything" tables (spec)

Owner brief of 2026-10-30. Code: `src/exports/repair/` (pure, account-neutral). Tests:
`tests/exports/repair/`. **Applies to every workspace, present and future.** Nothing in code names
an account, a platform owner, a divested business or a person: all of that comes from the
`WorkspaceProfile` (`core.py`) built from the workspace's own data (`profile.py`). The Becton
Dickinson (BD) workspace is only the regression case; its numbers below are checks, never
constants in code.

Tables: Job postings, People, Tech stack and teams, News, Sources, Web searches, Signals,
Coverage, From your mail and drive, plus the `Deliverables/` and `Internal - your files/` folders.

## Global rules (every table)

1. One row = one real entity. De-duplicate on a stable key (posting ID, profile URL, canonical
   URL, normalised technology name). Never twice because two providers returned it.
2. Every column that exists is filled or explicitly marked: `Not found`, `Not applicable`,
   `Not collected: <reason>`, `Unknown` (dates), `Outside window`. Never empty without a reason.
3. Dates are real event dates. `Published`, `Posted`, `First seen` come from the source. The
   collection time goes in a separate `Collected` column. Unknown real date: `Unknown`. Never copy
   the crawl date into a published-date field.
4. Entity resolution before counting. Every row has `Entity` (the account, a subsidiary, a
   divested business, or `Not the account`). Counts use only rows whose entity counts as the
   account. Domain collisions are excluded and logged.
5. Provenance on every row: `Source URL` and `Source type`. Rows without provenance are dropped
   and counted in the log.
6. Freshness: each workspace has a window (default 12 months ending on the run date). Rows
   outside are kept only flagged `Outside window` and excluded from counts.
7. Honest coverage: a blocked step shows `Not done` or `Partial` with the exact reason in
   Coverage; deliverables must not claim that area is done.
8. No fabrication: no names, titles, dates or URLs by guessing. Extracted fragments are never
   names or headlines.
9. Per-workspace out-of-scope rules (platforms, title exclusions) apply the same in the export as
   in the deliverables.

## Tables

### 1. Job postings (`JOBS_COLUMNS`)
One row per unique posting. Key = Posting ID (the numeric id at the end of a careers URL path such
as `.../159/<id>`, or a requisition `R-<number>` / `JR-...`; else the canonical URL). Rows sharing
the key merge; platforms union. `Title` is always the job title (never a platform name; a row whose
title equals a dictionary platform is a research fact, not a posting: the real title is recovered
from the URL slug or the row is dropped and logged). Platform dictionary = `config/parity/platforms.yaml`
(`Platforms`), each platform separate (MuleSoft is not Salesforce, Power BI is not Tableau);
"salesforce" meaning the sales team is not the platform; SAP is the capitalised word etc. `Lane(s)`
derive from platforms found in the posting text, never from generic words (data, analytics, AI)
alone. `Class` per platform: Build role / Admin and owner role / Working tool / User role (use
`parity.rules.classify` and the stored class). `Entity` from the profile's entity rules, the
posting host, and generic wording ("applying for a position with <Other>"); non-account entities
are excluded from all counts. `Status` only `Open` or `Closed`; an unresolved stored status
becomes `Closed (not found on site, <date>)` unless a live check says Open. Live board rows are
included. The full all-roles crawl is the second sheet `All postings (unfiltered)`
(`JOBS_ALL_COLUMNS`); the main sheet is platform postings only. Rows outside the window flagged.
XLSX is shipped: escape cells starting `= + - @` and the trailing-dash case.

### 2. People (`PEOPLE_COLUMNS`) + `Leadership` sheet (`LEADERSHIP_COLUMNS`)
Rows come only from profile / people-search data (`origin` profile, people_search, import), never
from news text (`news_fact`). Without people consent: an empty People table with one line
`Not collected: people data consent needed` and the Coverage people items `Not done`. `Name`:
validated person name (<= ~5 tokens, letters, no verbs/punctuation/titles/quotes); the descriptive
phrase goes in `Title as reported`. De-dupe on profile URL then normalised name + employer.
Seniority rule: Director and above, Head, Senior Manager; below only if the title names a lane
platform (`Meets seniority rule` Yes/No). Drop employer domains that are not the account's (log
count). Apply the profile's title exclusions. LinkedIn: URL or `Not found`. `Priority (A/B/C)`,
`Lane`, `Level` filled. `Leadership` sheet: press-release/filing executives with start/end dates,
checked against the newest source: Current / Departed / Moved to divested business / Unverified.

### 3. Tech stack and teams: three sheets
* `Platform Ranking` (`RANKING_COLUMNS`): base = unique account postings naming any dictionary
  platform. Rank A = >=15% of base and >=5 postings; B = >=5% and >=3; C = >=2; one posting =
  "Seen once". `Share of base`, `Rank` are spreadsheet **formulas on the counts** in XLSX (values
  in CSV). Every lane platform found in postings appears.
* `Confirmed stack` (`STACK_COLUMNS`): postings-derived technologies merged with detections;
  `Cross check` = Both / Postings only / Detection only on 100% of rows. Website detections are
  `Source type = Website detection` with `Detected domain`; drop detections from domains that are
  not the account's; reject technology values > 40 characters or containing a verb phrase (log);
  normalise names so duplicates (e.g. `AWS Athena`/`Athena`, `Azure ML`/`Azure Machine Learning`)
  collapse to one row.
* `Platform teams` (`TEAM_COLUMNS`): top 25 teams by postings whose public records name a lane
  platform.

### 4. News (`NEWS_COLUMNS`) + `News facts` sheet (`NEWS_FACT_COLUMNS`)
One row per article/filing (key = canonical URL). Extracted facts live in `News facts`, not as news
rows. `Headline` = the real article title (never an extracted fragment; if never read:
`Not found`). `Published` = date parsed from the page, URL or filing metadata, else `Unknown`;
`Collected` separate. `Within window (Y/N)`; `Superseded by` links to the newer item when a fact was
replaced. Grades: `Confirmed` only from primary sources (company release, filing, company site);
`Probable` from reputable trade press; `Lead` for everything else, incl. transcripts on aggregator
sites.

### 5. Sources (`SOURCES_COLUMNS`)
`Title` from the page title/filing name; `Publisher` from the domain map; `Published` per rule 3
(`Unknown` when unknown); job pages go in their own section (separate sheet `Job pages`), not
among the evidence sources. Every source cited in a deliverable (S1..Sn) resolves to a row; every
Confirmed fact cites one.

### 6. Web searches (`SEARCH_COLUMNS`)
One row per unique normalised query: providers joined, first run, number of runs, total pages
returned, sites, used Y/N, purpose (coverage item #).

### 7. Signals (`SIGNAL_COLUMNS`)
Generated from Job postings, News, People/Leadership and Tech: types in `SIGNAL_TYPES`. Each signal
links at least one source code; signals without a source are dropped and logged.

### 8. Coverage (`COVERAGE_COLUMNS`)
Single source of truth. Adds `Evidence (row counts / source codes)` and `Last run`. A coverage item
is `Done` only with evidence rows; otherwise it is downgraded to `Partial`/`Not checked` with the
reason. A deliverable section may say "done" only if its Coverage item is `Done`; else it must carry
a visible `Partial: <reason>` note (`DeliverableClaim`).

### 9. From your mail and drive (`MAIL_COLUMNS`)
Connected: matched items, marked `Internal: do not share` (file goes to `Internal - your files/`).
Not connected: one row `Not collected: mail and drive not connected` and Coverage items 1-2
`Not done`.

### 10. Packaging
Zip: `README.txt`, `Deliverables/`, `Tables/` (each table as CSV and XLSX), `Internal - your
files/` (internal docs and the mail/drive table), `Logs/` (`repair_report.md`, `exclusions.csv`).
The README is generated from the members actually written; before zipping every path the README
lists must exist or the export fails. CSV: UTF-8 with BOM, all fields quoted, one header row.
XLSX: cells beginning `= + - @` escaped; a shareable workbook is never held back for a dash: the
offending cell is fixed (`safe_cell`), then re-checked.

## Acceptance checks (`checks.py`, all must pass per workspace)
No duplicate keys; no empty cell without a reason placeholder; no Published/Posted equal to
Collected unless the source shows it; People names valid, 0 rows from news, LinkedIn filled or
`Not found`; Jobs: Title never a platform name, Status in {Open, Closed*}, every row has Entity,
non-account rows excluded from counts; Tech: every lane platform found in postings is in the
ranking, Cross check on 100%; Sources: Title/Publisher/Published filled or `Unknown` on 100%, every
deliverable citation resolves; Coverage agrees with deliverable claims; zip contains every path the
README lists (incl. `Job postings.xlsx`); counts in Tables equal counts quoted in deliverables.

## BD regression (a check, same base dates 19 Oct 2025 - 19 Oct 2026; never hard-coded)
Salesforce postings (BD entity only) 551 (92 open); SAP 1,068 (150 open); build roles Salesforce
11, SAP 10, MuleSoft 3, Workday 8, Veeva 1, Tableau 1, ServiceNow 0; Waters postings 569 plus 116
pre-close, never counted as BD; People 736 total, 424 meeting the seniority rule, 80 Priority A
senior; Platform Ranking base 1,904, A = SAP, Salesforce, Power BI, B = Microsoft Azure. A re-run on
a later date reports both sets of numbers and explains each difference.

## Outputs
Repaired zip; `Logs/repair_report.md` (before/after per table, acceptance results, every pipeline
change, what is still Not done / Partial and what the user must enable); `Logs/exclusions.csv`
(every dropped or excluded row with key and reason).

## Implementation (2026-10-31)

Code, all in `src/exports/repair/` and pure except `readers.py`:

| Module | Job |
| --- | --- |
| `core.py`, `records.py`, `keys.py` | the contract: profile, table result, placeholders, schemas, raw records, stable keys |
| `profile.py` | `WorkspaceProfile` from the account row, its entity lock, the skill scope, the tenant's people consent and the user's mail connection (`load_profile`); `profile_from_parts` is the pure form |
| `readers.py` | the raw tables of one workspace as records (`load_input`) |
| `entity.py`, `jobs.py`, `tech.py` | postings (one row per posting ID, platform dictionary from `config/parity/platforms.yaml`, class, entity, status, window), ranking, confirmed stack, teams |
| `names.py`, `people.py` | person-name validation (strict for text, relaxed for a provider's name field), People and Leadership |
| `dates.py`, `news.py`, `sources.py`, `searches.py` | articles and facts, the sources register, unique searches |
| `signals.py`, `coverage.py`, `mail.py` | signals from the other tables, coverage as the single source of truth, mail and drive |
| `package.py`, `checks.py`, `report.py`, `changes.py` | CSV/XLSX writers (formula cells, escaping, the dash fix), README generated and validated, acceptance checks, report and exclusions log |
| `pipeline.py` | the fixed order; `repair()` returns tables, checks and report |
| `offline.py` | readers of an old export zip and of a finished reference workspace (`scripts/repair/regress.py`) |

Wiring: `replica/downloads.py` `_fill` loads the profile and the records, runs `pipeline.repair`
in a worker thread, writes `package.build_members` and a README made from the members actually
written (`package.finish_readme` refuses a README that lists a missing path).

### Per-workspace settings (data, never code)

Everything is derived from the workspace itself. What cannot be derived is an owner setting in the
account's `watch_config_json`:

* `short_name`: the name the account's own text uses (default: the legal name without its legal
  form);
* `aliases`, `domains`: extra names and registrable domains of the account;
* `entities`: a list of `{label, patterns, counts_as_account, before}`: a divested business, a
  subsidiary, a pre-close unit (rows first seen before `before`). A posting whose own text says it
  is "applying for a position with <another employer>" is tagged to that employer without a rule.
  Subsidiaries in the stored entity lock count as the account.

### Decisions this build made

1. "Meets seniority rule" is the level alone (Director and above, Head, Senior Manager). A person
   below it is listed only when the title names a lane platform (and says No); otherwise the row is
   dropped and logged. This is how the reference People List reads (424 Yes of 736).
2. A profile or people-search name field accepts a bracketed nickname, trailing credentials, a
   capitalised surname, a trailing initial and a two-letter surname; text (news, phrases) never
   does.
3. A posting whose text names another employer is excluded from every count even when an earlier
   reference counted it as the account (BD: 11 postings, explained in the report).
4. A news signal below Probable must read as a whole sentence.

### Known limits

* The deliverables themselves are not rebuilt by this stage. A section that presents an area as
  done while Coverage says otherwise is reported (Coverage > Deliverable claims, and the check
  `coverage_agrees_with_deliverables`); adding the visible `Partial: <reason>` note belongs to the
  deliverable builders (`exports/readers`).
* A News headline is the title the sources register holds. Most runs store no page title when a
  page is read, so those articles show `Not found` (never an extracted fragment). Fix upstream: store
  the page title at read time.
* Pre-close postings of a divested unit need the owner's `entities` rule with a `before` date.
