# Prompt: build the Export builders (Word and Excel files and the workspace zip, rendered from database rows, same look for every account), production grade

Paste everything below the line into Claude Code, opened in the project repo (built with the `account-intelligence-platform` skill).

---

Build the **Export builders**: port the existing **Node docx library** and the **Python openpyxl workbook logic** so they **read from database rows** and produce the standing file set: **Account Summary, Sources and Evidence, People List, Job Postings, Tech Stack and Teams**, plus the **workspace zip**, with the **same look for every account**. This is backend work (it implements `BuilderPort`; the full quality gate service stays behind `GatePort`). Write production-grade code and prove it with a real harness. Never invent facts about the process, the look, the providers or the account, and never let a model write, change or number anything at build time.

## 0. Ground rules

1. Read `.claude/skills/account-intelligence-platform/SKILL.md` first and obey it. Then read `references/01-product-and-process.md` (deliverables, account summary order, quality gate, rules), `references/05-data-model.md`, `references/06-evidence-and-grading.md`, `references/03-tenancy-isolation-security.md`, `references/09-private-context-phase-b.md` (two-output rule, Internal Only), `references/12-open-decisions-and-verification.md` (D-01, D-06, D-10), `references/13-starter-code-and-known-defects.md`, `assets/client_output_denylist.txt`, `scripts/scan_client_output.py`, `BUILD_LOG.md`, `docs/STATUS.md`. If `docs/sources/` holds the PRD, read section 3.4 (Deliverables, Quality gate, Constraints), 4.1 (Build row) and 4.2 steps 11 and 12 and cite them. Follow the skill's work loop: plan in five lines, tests first, smallest increment, run the gates, log.
2. **Fixed by the skill:** the database enforces isolation (`tenant_id NOT NULL`, forced RLS, roles `TO app_rw`, access through `tenant_txn`); the two-output rule (public files never contain private or Internal rows); client-facing output never names data brokers, credit counts, call recordings, client staff or other accounts, and is attributed to "6th agents"; only Confirmed claims may be stated to a prospect; every fact, posting, article and person carries its URL in the same row; every count names its base and is computed in code; nothing is deleted or overwritten (a redelivery adds a new version).
3. **The builders never call an LLM, never touch the network, and never read the wall clock.** They render stored rows deterministically. Narrative and hypotheses are **content rows written earlier by the distill step**; the builders only validate and render them.
4. No real provider calls, no real LLM calls, no real spend, no real tenant data. Fakes, `synthetic` fixtures and hermetic containers only.
5. Never claim a gate passed that you did not run. Never weaken or delete a test to pass. Commit on branch `feat/export-builders` in small Conventional Commits. Do not push.

## 1. Preflight (before writing code)

1. **Find the legacy library.** Look for the existing skill assets (the Word builder library in Node, the people workbook logic, the jobs workbook logic, `rowcheck.py`, `doc_checks.py`, `third_party_scan.py`, `strip_tool_names.py`, the output spec) under `docs/legacy/`, `docs/sources/`, or anywhere in the repo. **They are not part of the skill I gave you.** If they are missing, **STOP for the port and ask me for the exact files**: the Node docx library folder, the two workbook scripts, and one real delivered file set (Word, Excel, zip) from a September account as the visual baseline. Do not reconstruct "the look" from imagination. Meanwhile build everything that does not depend on the look (models, readers, policy, numbers, citations, snapshot, workspace, verify, determinism) with a neutral labelled theme.
2. Print a table of what exists: facts and `fact_evidence` (supporting sentence), `sources` with stable codes, `people` and its pipeline counts, `job_postings` and its counts, `tech_detections`, any teams data, the tracker (`view` for clients), grading (`statement_policy`, `display_label`), the narrative and hypothesis content rows from the distill step, `BuilderPort` and `GatePort`, the payload store, `deliverables` tables, `tenant_txn`, the isolation suite, the workflow stage `s11_build`.
- Missing dependency: define a typed `Protocol` in `src/exports/ports.py` and an in-memory fake in `tests/fakes/`. Ports: `ContentReader` (read models per file, from one snapshot), `DeliverableStore` (put-if-absent, get, list; versioned), `PdfRenderPort` and `RecalcPort` (optional checks), `RunInputPort`, `Clock`. Production adapters that do not exist raise `NotConfigured`; the run then ends PARTIAL with reason `builders_not_configured`, never a fake success.
- **One implementation per rule.** Reuse the grading engine's `statement_policy`, the tracker's client view, and the job and people count modules; do not copy them. If the workflow already contains build code, move it here and leave a thin re-export.
- If `deliverables` or content tables exist, **extend them expand-only** (add columns, constraints, indexes; never drop or alter an existing column; ask first if unavoidable).

## 2. Anti-hallucination protocol (build time)

1. File names, contents, section order, the gate checks and the standing rules come only from the PRD and the skill (section 0.1). **The sources do not give**: the look (fonts, colours, spacing, headers, footers, cover, table styling, column widths), the exact workbook columns beyond those the PRD names, the stale-phrase list, the banned-dash definition, the abbreviation list, the Salesforce-partner phrase list, the `RESTORE.md` text, or what "build scripts" means in a service. All of it is configuration with a `source` field: **extracted from the legacy file and line** where the legacy library defines it, `source: TODO, needs product owner` where nobody does, `status: proposal` where I propose a default. Never invent styling values or phrase lists.
2. **Port, do not rewrite.** Keep the Node docx renderer and the openpyxl layout logic where they work; change only the data source (rows in, instead of values built inside a conversation). First write **characterization tests** that capture what the legacy code produces for the legacy sample inputs; the port must reproduce its structure and style fingerprint (section 9.4). Preserve column order, names, widths and number formats exactly.
3. **Proposals of mine, not facts from the sources** (config marked `status: proposal`, ADR, register entries at the next free numbers; earlier prompts used D-12 to D-23, so start at **D-24** unless the register shows otherwise):
   - **D-24 content rules:** banned-dash scope (proposal: authored prose only; evidence quotes stay verbatim and are rendered as quotes), the stale-phrase list, the abbreviation list, the Salesforce-partner phrase list, the policy for a restricted term inside a verbatim source quote (proposal: **withhold the sentence, keep the source link, count and list it**, never alter a quote).
   - **D-25 workspace zip:** contents `RESTORE.md`, the deliverables, `MANIFEST.json`, notes, and the **render-model data** in place of "build scripts" (the builder code is not shipped); **raw broker payloads excluded by default** (the PRD lists raw data with neutral names, the SaaS document says to share graded facts and links, not raw exports, and broker terms D-10 are open); the neutral-name copy path is built and tested but off until D-10 is answered; payloads from the organisation provider are never included in any case (adapter rule).
   - **D-26 renderer:** keep the Node docx renderer (as a sandboxed subprocess behind a JSON contract) rather than re-implementing Word output in Python; reversible behind the port.
4. The look is **one theme file** (`config/export_theme.yaml`) with a content hash recorded on every deliverable. No style literal appears in any renderer outside the theme loader; a static test enforces it.
5. No vendor or broker name in `src/exports/`, config keys, file contents, document properties or object keys. Provider identity is a `provider_code` in the internal ledger only.
6. Verify every library fact against the installed package (docx, openpyxl, zip handling, LibreOffice flags); mark anything unverified `[VERIFY]` in `docs/open-items.md`. Do not state performance numbers; measure them.

## 3. Anti-hallucination protocol (run time): the BuildGuard

Code rules, each with at least one "lying content" test (section 9.6).

| # | Rule |
| --- | --- |
| B1 | **One consistent snapshot.** All five files and the zip read from a single `REPEATABLE READ` transaction under `tenant_txn`; the manifest records a snapshot hash. A fact written during a build cannot make the Summary disagree with the People List. |
| B2 | **No model, no network, no clock at build time.** Dates come from the run's `as_of` and stored timestamps; document properties are fixed (author and company "6th agents", no machine, user or tool names). |
| B3 | **Row conservation.** Every selected row appears exactly once in its file, with its row id in the manifest; the manifest counts equal the database counts for the snapshot. A row cap or limit reached **fails the build** (`RowCapExceeded`); nothing is truncated silently. A section with no rows renders an explicit "None found" derived from the tracker proof, never a blank. |
| B4 | **Every rendered row carries its URL** and a stable source code (`S-0001`) from the source registry. A row without a URL is rejected and the build fails with the list. |
| B5 | **Citations resolve.** Every cited source code exists in the Evidence file; no orphan citation; every graded row cites at least one source; codes are never invented or renumbered. |
| B6 | **Numbers are rendered, never typed.** A number registry ties every rendered number to a count computed in code (people counts, job counts, tracker tally) and each names its base. A number in authored text that is not in the registry, or not in the cited rows, fails the build (the narrative check). |
| B7 | **Statement policy.** Only Confirmed rows are rendered as statements. Probable, Hypothesis, Lead are rendered as questions or labelled by the grading `statement_policy`; every Hypothesis shows its confirming question; aggregator sources are labelled; a grade or read flag that disagrees with the vocabulary or with `read_in_primary_this_run` is refused at read time. |
| B8 | **Two-output rule.** Client files read only `public` rows. Private, Internal and Internal Only rows never reach any file; the Internal Only Word file is **never built here** and a negative test proves it cannot enter the zip. |
| B9 | **Identity and leak scrub.** Denylist terms, provider codes, credit or cost figures, call refs, recordings, other accounts, client staff names ("write Client"), and excluded, review, unresolved, merged or erased people never appear. Sensitive findings carry the standing "preparation, never openers" sentence; for a tenant flagged as a Salesforce partner, flagged framings fail the build. |
| B10 | **Deterministic and reproducible.** The same snapshot and theme give byte-identical files: explicit ordering with tie-breakers, no random ids, fixed zip entry order and times, fixed document properties. |
| B11 | **Safe cell and run content.** Untrusted text (descriptions, names, sentences) is sanitised: XML-invalid and control characters removed; text that could execute as a spreadsheet formula (starts with `=`, `+`, `-`, `@`, tab or carriage return) is written as a string cell; Excel's cell size limit is respected by explicit, counted continuation, never silent truncation. |
| B12 | **Totals are formulas, checked.** Workbook totals are formulas over the data sheet; the verification recalculates and asserts zero errors and totals equal to the code-computed counts. |
| B13 | **Safe zip.** Relative sanitised names, no absolute paths, no `..`, no symlinks, entry and total size caps, `MANIFEST.json` with a hash for every member; every docx and xlsx member passes the scan. |
| B14 | **Idempotent and append-only.** A build is keyed by `(run_id, content_hash)`; repeating it returns the existing version and writes nothing; a redelivery stores a **new version** with `supersedes`, never overwriting; storage uses put-if-absent. |
| B15 | **Fail closed.** If any file fails, the set is not delivered as complete and the zip is not built; the result lists every error; the run ends PARTIAL `build_failed`. The renderer subprocess has a timeout, bounded output, a cleared environment and **no database credentials**. |

## 4. Architecture to build

```
src/exports/
  config.py       loads and validates theme and rules; content hashes recorded on every deliverable
  errors.py       ExportError > MissingContent, BannedContent, OrphanCitation, RowCapExceeded, LeakError, RenderError, SchemaViolation, ConfigError, NotConfigured
  models.py       render models, frozen pydantic, extra='forbid': SummaryModel, EvidenceModel, PeopleModel, JobsModel, TechModel, WorkspaceModel; BuildManifest; Deliverable
  snapshot.py     one REPEATABLE READ snapshot and its hash
  readers/        summary.py evidence.py people.py jobs.py tech.py tracker.py: keyset-paginated rows to render models
  policy.py       statement policy, visibility filter, leak and identity scrub, banned and stale checks, excluded-name guard
  numbers.py      number registry, base-naming sentence templates
  citations.py    source-code resolution, orphan detection
  fingerprint.py  style and layout fingerprints for docx and xlsx (the "same look" test)
  word/           render_docx(model) -> bytes (subprocess wrapper); schema/*.json; node/ (the ported library, lockfile pinned)
  excel/          common.py people.py jobs.py tech.py: styles from the theme, injection-safe cells, formula totals
  workspace.py    deterministic zip, RESTORE.md, MANIFEST.json
  verify.py       self-checks used by the builders and the harness (full gate stays behind GatePort)
  store.py        DeliverableStore usage: versioned, put-if-absent
  builder.py      build_all(run_id) -> BuildResult; implements BuilderPort
  change_note.py  pure: one line on what changed versus the previous manifest
  ports.py        Protocols
src/workflow/stages/s11_build.py   thin adapter (if the workflow exists)
config/{export_theme.yaml, export_rules.yaml}
docs/exports/{theme.md, files.md}  generated; a test fails if stale
migrations/                        one expand-only revision
```
Purity rule: `policy`, `numbers`, `citations`, `fingerprint`, `change_note` and the readers' mapping functions do no I/O and read no clock or environment. The Node library never receives anything but a validated render model on stdin.

## 5. The files (contents from the PRD; do not embellish)

| File | Format | Must contain |
| --- | --- | --- |
| `<Account>_Account_Summary` | Word | Plain-language narrative first; caveats on interpretation; graded tables lane by lane; key people table (priority A and senior); Findings from LinkedIn as a separate section next to the people list; executive voice; hypotheses with confirming questions; first-call questions; coverage tracker sorted with the most valuable open work first (from the tracker's client view, never typed) |
| `<Account>_Sources_and_Evidence` | Word | Every source: stable code, link, date, grade, the exact sentence relied on; job descriptions read; news items verified at source |
| `<Account>_People_List` | Excel | Deduplicated senior people: title, level, lane, priority, profile URL, source code; a **Summary sheet with counts by exclusion status and reason, never names** |
| `<Account>_Job_Postings` | Excel | 12 months of open and closed postings: class, platform sentence, full description, link, plus status, dates, platform, source code; counts with their base |
| `<Account>_Tech_Stack_and_Teams` | Excel | Technology detections with first and last seen and the cross-check against postings; a date range alone is labelled a Lead; a Teams sheet only if team data exists, otherwise a visible "None recorded" note |
| `<Account>_workspace.zip` | Zip | Per D-25 |
Narrative, caveat, hypothesis, first-call-question and "what changes a decision" text are **stored content rows** (`report_sections`: kind, order, text, cited source codes, fact ids), written by the distill step and read-only here. If they do not exist, create the table (expand-only) and have the builders refuse the Summary with `MissingContent`; list "distill writes `report_sections`" as the first follow-up. Plain English on every heading; abbreviations written out on first use (checked against the configured list); internal program names explained as internal names.

## 6. Database (one expand-only revision; follow `scripts/lint_migrations.py` rules R1 to R5 including `lint:covers`)
1. `deliverables` (append-only, INSERT and SELECT grants only): run, account, kind, version, `supersedes`, filename, `sha256`, size, object key (neutral, no broker or provider names), `manifest_json`, `content_hash`, `snapshot_hash`, `theme_hash`, `built_at` from the injected clock.
2. `report_sections` (append-only, versioned per run): the distill contract above.
3. If `facts` has no lane column, add a nullable `lane` (expand-only) so the Summary can group lane by lane; list the change for `record_graded_fact` as a follow-up.
4. Forced RLS (`USING` and `WITH CHECK`, `TO app_rw`), `tenant_id NOT NULL`, no DELETE. Extend `tests/isolation`; `scripts/check_rls_catalog.sql` must return no rows.

## 7. Loops (all bounded, recorded, testable)
- **Row streaming:** keyset pagination in batches until rows read equal the snapshot count; the row cap fails the build (B3). Memory stays bounded by the batch size.
- **Per-file builds** are independent and run in a fixed order; a failure stops the zip (B15).
- **Renderer subprocess:** one call per Word file, a timeout, at most **one** re-run (the render is deterministic), then `RenderError`; stdout and stderr are size-capped.
- **Optional render check:** the PDF conversion has a timeout and at most one re-run; a missing converter makes the check `not_run` with a reason, never "passed".
- **Gate fix loop** (owned by the workflow, at most 3 build, gate, rebuild cycles) relies on determinism: a rebuild after a content fix changes only what the fix changes. The builders add **no** retry or loop of their own beyond these.
- Every loop records its exit reason (`converged`, `cap_reached`, `budget`, `deadline`) in the build result.

## 8. Integration
1. `build_all(run_id)` implements `BuilderPort` and returns `BuildResult` (status, deliverables, `BuildManifest`, errors). The manifest carries row counts per file, source codes cited, every rendered number with its base, the tracker tally, file hashes and the snapshot hash, so the gate can reconcile without reopening the files.
2. The gate keeps its own checks behind `GatePort`; here, `verify.py` implements self-checks the harness and the builders use: manifest reconciliation, style fingerprint, `scripts/scan_client_output.py` over every file and the zip, and, when installed, a Word to PDF render with a row-presence check and a workbook recalculation.
3. **Redelivery:** the complete file set is rebuilt every time; `change_note` computes one line (rows added, removed, changed per file) from the previous and new manifests.
4. Storage goes through `DeliverableStore` (put-if-absent, versioned); the GCS adapter belongs to the cloud integration work and is listed if absent. Download links and roles are an API follow-up.
5. Metrics by file kind and outcome only, no tenant label. Row text is never logged at info level (a canary test).

## 9. Test harness (build before the builders)
Location `tests/exports/`, real Postgres, injected `Clock`, fakes from `tests/fakes/`. Fixtures under `tests/fixtures/synthetic/exports/`, every file named and labelled `synthetic`.
1. **Synthetic accounts with hand-written oracles:** rich (thousands of rows; descriptions over the cell limit; Unicode, right-to-left text, emoji; XML-invalid characters; strings such as `=HYPERLINK(...)` and `@SUM(...)`), sparse (empty sections), hostile (injection text, a broker name inside a source quote, banned dashes in authored text), provider outage, conflicting sources, a Salesforce-partner tenant, and two tenants on the same domain.
2. **Reader and policy tests:** mapping is total and pure; ordering rules with tie-breakers; visibility filter; statement policy; excluded-name guard.
3. **Row conservation (property tests, hypothesis):** for any generated row set every row appears exactly once, order is stable, no cell exceeds the limit, no generated string becomes an executable formula, the zip has no unsafe member, the manifest counts equal the input, and building twice gives identical hashes.
4. **Same look:** build two different accounts; the style and layout fingerprints (styles used, fonts, sizes, colours, margins, table properties, column widths, number formats, freeze panes, print setup, headers and footers) are **identical** and differ only in content; changing the theme changes every file consistently; a static test fails on any style literal outside the theme loader. **Legacy parity** (characterization tests and, once supplied, the real baseline files) is wired and reported as **Blocked: needs the legacy library and one real delivered file set** until then.
5. **Determinism:** two builds from one snapshot give byte-identical files and zip; document properties contain only the allowed attribution and no tool, machine or user name.
6. **Lying content, every B-rule:** an orphan citation; a number not in the registry or not in the cited rows; Confirmed without the primary-read flag; a private fact in a public file; an excluded person; a Probable claim phrased as a statement; a Hypothesis without its question; an aggregator source not labelled; a broker name, credit count or call reference in text; a client staff name; formula-like strings; a banned dash in authored text; a row without a URL; a narrative sentence without a source code; a flagged framing for a Salesforce-partner tenant; a restricted term inside a verbatim quote (withheld, counted, listed). Each ends in its typed error or its documented handling, and **no partial set is marked complete**.
7. **Integration checks:** Word to PDF render and a row-presence check that every source code and every table row key appears once in order (the lost-row-at-page-break defect), workbook recalculation with zero errors and totals equal to the counts, `scripts/scan_client_output.py` over the unzipped package (document XML, relationships, cells, properties) finds nothing, the Internal Only file cannot enter the zip. When LibreOffice or poppler is not installed these report `not_run (reason)`, not passed.
8. **Database tests:** a concurrent writer during a build cannot break snapshot consistency; `deliverables` refuses UPDATE and DELETE; a repeat build writes nothing; a redelivery adds a version with `supersedes`; a kill mid-build leaves no deliverable marked complete and no orphan object; two tenants never cross.
9. **Renderer hardening:** timeout, malformed output, oversized output, non-zero exit, schema-violating input; the process runs with a cleared environment and no database credentials; the same JSON Schema validates on the Python and Node sides.
10. **Scale smoke:** a large synthetic account (for example 10,000 postings and 5,000 sources) builds within a memory cap; measure and report time and peak memory, do not assert numbers I did not give.
11. **Mutation check:** run `mutmut` (or the project's tool) on `policy.py`, `numbers.py`, `citations.py`, `change_note.py`, `workspace.py` if it installs; list surviving mutants or say it could not run.
Coverage: at least 90% on `src/exports/`, **100% on `policy`, `numbers`, `citations`, `fingerprint`, `change_note` and the zip-safety code**.

## 10. Development loop (bounded)
For each component repeat at most **5 iterations**: write or extend failing tests; smallest implementation; run its tests, `ruff`, `mypy --strict src/exports` and the isolation suite; if red, change the **code**. After 5 red iterations on one component, STOP and report the failing test, its output, what you tried and your hypothesis. Do not weaken the test, mock the unit under test, skip it, or mark it `xfail` (except for a defect already recorded in `references/13` or an ADR).
Build order: `config`, `errors`, `models`, `policy`, `numbers`, `citations`, `fingerprint`, `change_note`, `ports` and fakes, corpus and oracles, `snapshot`, `readers`, `excel/` (people, jobs, tech), `workspace`, `word/` (contract, then the ported renderer), `verify`, migration, `store`, `builder`, integration checks, docs generation.

## 11. Production requirements
Type hints everywhere and `mypy --strict`; no bare `except`; no global mutable state; no clock or randomness in pure modules; bounded memory; Node dependencies pinned by lockfile and audited; config validated at startup and hashed; structured logs with run id as the trace id; one span per file and per verification; idempotent migration that runs twice cleanly; every public function documented with its guarantees; every error typed.

## 12. Gates before you finish
Run and report real results: skill self-test; `validate_assets.py`; `lint_migrations.py`; `check_rls_catalog.sql` (no rows); `ruff check`; `mypy --strict src/exports`; the Node renderer's own lint and tests; isolation suite; `pytest tests/exports` with coverage; the full `pytest -m "not eval"`. Confirm: every config entry has a `source` or `status`; two accounts have identical fingerprints; builds are byte-identical; every B-rule has a passing lying test; no vendor name in `src/exports/` or in any produced file; the docs are current.

## 13. Deliverables
`src/exports/`, the ported Node renderer, the stage adapter, the migration, `config/` files, `docs/exports/`, ADRs (what was ported and what changed, theme extraction, content rules, zip contents, renderer sandbox), the updated open-decision register (D-24, D-25, D-26), tests and fixtures, updated `BUILD_LOG.md` and `docs/STATUS.md`.

## 14. Stop rules
Stop and ask me if: the legacy library or the baseline files are missing and a decision depends on them (build the rest first and list exactly what to supply); a change would drop or alter an existing column; D-24 or D-25 blocks a file without a usable default; a content rule would force altering a verbatim source quote; the skill and another source conflict outside the register; you want to alter the architecture in section 0.2; real credentials, spend or tenant data would be needed; a gate stays red after 5 iterations.

## 15. Final reply (short)
A table of the files (Summary, Evidence, People, Jobs, Tech, zip: Done, Partial, Blocked, and what each depends on); gates run with results; what is **proven by tests** versus assumed (state precisely: rows in equals rows out, one snapshot, byte-identical rebuilds, no model or network at build time, every number from a count with its base, no orphan citation, no leak, safe cells and zip, fail closed); what is **not proven** (the real look and legacy parity until the legacy library and a real file set are supplied; the full gate; render checks if the converter was missing); the decisions I must make (D-24, D-25, D-26); what I must supply (legacy library and scripts, a real delivered file set, stale phrases, abbreviation list, `RESTORE.md` text); integration follow-ups (distill writes `report_sections`, GCS store adapter, download API); open `[VERIFY]` items; commits; the next work package (the quality gate service).
