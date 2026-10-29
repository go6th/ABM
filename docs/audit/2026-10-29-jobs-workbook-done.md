# Job Postings workbook: done (2026-10-29)

The Job Postings workbook now has the reference file's shape (sheets Summary and Jobs, 14 columns,
widths, freeze `C2`, hyperlinks, sort order, Summary matrix of COUNTIFS), the owner's two added
columns, per-account entity rules as data, the reference's platform lexicon, and the reference's
Working tool / User role rule as the last classifier. Nothing was committed, no server or paid run
was started. The reference account's name appears nowhere in code, config, fixtures or tests.

## What changed

Workbook (`src/exports/excel/jobs.py`, `src/exports/readers/jobs.py`, `config/export_rules.yaml`)
* Jobs columns in the reference order and widths (12, 44, 18, 20, 9, 11, 11, 26, 11, 26, 50, 70, 60,
  13), then `Archived link` (16, text `Open archived copy`, hyperlink = our stored copy, blank when
  none) and `New since last research` (14, `Yes` or blank; blank column when no previous
  research), then the product-only `Ref` and `Source code` to the right (Ref is the
  row-conservation key; commented in the config). `Found by` is no longer printed.
* `Link` shows `Open posting` with the address as hyperlink. `First seen` / `Last seen` are ISO
  date text (first seen falls back to the posting date, then to the capture date for a posting
  only the live board holds). `Seniority` as given. Rows sorted First seen descending, then
  posting key; refs `J-0001` follow that order. New rows get the theme fill `new_row`.
* `Platforms named` in the fixed order Salesforce, MuleSoft, ServiceNow, SAP, Tableau, Veeva,
  Workday, then others alphabetically. `Platform sentences` as `Platform: s1 | s2 || Other: s3`
  (4 per platform, bullet marks removed, whitespace collapsed, 3,000 characters, no newline).
  `What the posting says (read)` only for Build and Admin and owner roles read in full.
* Summary exactly as the reference: title row 1, five account-neutral paragraphs merged A:H
  (rows 3 to 7), navy header row 10 `Platform (named anywhere), {prefix} positions only | Total |
  Closed | Open | Build role | Admin and owner role | Working tool | User role`, the seven
  platforms (rows 11 to 17) as `COUNTIFS(Jobs!$C..., "*Name*", Jobs!$J..., "<prefix>*", ...)`,
  three row-count lines (rows 19 to 21), column A 52 wide, B to H 18. Microsoft Dynamics and the
  `All rows` line and the extra class columns are gone. `Counts` and `Long text` stay after the
  reference sheets (owner decision pending).
* Config additions in `src/exports/config.py` `JobsBook` (small targeted edit): `matrix_classes`,
  `read_text_classes`, `sentences_per_platform`, `sentence_chars`, `summary_span`,
  `summary_label_width`, `summary_count_width`. Models: `JobRow` gains `last_seen`,
  `first_seen_text`, `last_seen_text`, `seniority`, `archive_url`, `new_since_previous`,
  `new_flag`; `JobsModel` gains `prefix`, `matrix_heading`.

Entity rules as data
* Source: the stored entity lock (`SnapshotContent.entity_lock`, already read by the exports):
  `entities: [{label, patterns (case-sensitive regexes), before (ISO date on first seen),
  counts_as_account}]` and optional `entity_prefix` (the short name; must start the account name).
  Rules apply first over title + description, then the host logic. A posting with no date is not
  matched by a rule with `before`. `counts_as_account` must agree with the label (starts with the
  prefix or not) because the matrix counts `"<prefix>*"`. A bad rule (no label or pattern,
  uncompilable pattern, bad date, disagreement) is left out and listed in the manifest as withheld
  block `jobs.entities`, never guessed.

Lexicon (`config/job_lanes.yaml`; `config/platform_dictionary.yaml` was brought to the same
patterns by the Tech agent)
* Workday added as lane `other` (live-board keyword Workday: one more board search per run).
  The people pipeline would have built title searches for it; the lane carries
  `people_search: false` and `src/people/config.py` skips such lanes (one-line guard, so no extra
  paid people searches).
* Bare `Ariba` and `Concur` under SAP; `eTMF`, `CTMS` under Veeva; `Health Cloud`, `Commerce
  Cloud` under Salesforce; alias `Apex` removed. SAP is matched in its own case
  (`case_sensitive: true` per platform; `jobs.match.term_pattern(term, case_sensitive)`,
  `match_platforms` / `extract_platform_sentences` take the set).

Classification (`src/jobs/heuristic.py` new, `classify.py`, `pipeline.py`, `config.py`,
`claims.py`, `config/posting_rubric.yaml`)
* The keyword rules no longer decide a class. `title_candidate` applies them to the TITLE only:
  a Build or Admin and owner cue makes a posting a candidate for the extractor.
* Order in `_classify`: extractor when the caps are off (every posting) or the title is a
  candidate and budget remains (second pass still confirms Build and Admin and owner); otherwise,
  and whenever the extractor gives no verified class (unclear, schema, disagreement), the
  heuristic: Working tool when the platform sentence window (previous `.` or `*` to next `.`,
  whitespace collapsed, 4 windows per platform, 3,000 characters) holds any cue word, else User
  role. Stored `classified_by="heuristic"`, reason `heuristic_window`, `heuristic_after_cap` or
  `heuristic_after_<why>`. Cues, window and provenance are data in `posting_rubric.yaml heuristic`.
* A heuristic class makes no grading claim (a keyword window is not a reading).
* Migration `0088_posting_heuristic` (+ `.down.sql`, revision file; down_revision `0087`) widens
  the `classified_by` check. `EXPECTED_REVISION` is already `0090` (other agents), so it was not
  touched; the README landmark list names 0088.
* `_class_key` returns the stored class (the derived "title only" / "no lane platform" labels are
  gone; `Unclassified` remains only for a posting the pipeline errored on).

Docs regenerated: `docs/exports/files.md`, `docs/exports/theme.md` (`python -m exports.docs`),
`docs/jobs/rubric.md` (`scripts/gen_jobs_docs.py`, whose text now describes triage and the
heuristic). `scripts/gen_loops.py --check` passes. No table changed, so the isolation matrix is
untouched (the generator needs a database; not run).

## Tests
New: `tests/jobs/test_heuristic.py` (35: cue words, window rule, caps, triage, order of the
classifiers on the golden run, extractor fallbacks, lexicon parity, case sensitivity),
`tests/exports/test_jobs_workbook.py` (21: manifest equality of sheets, headers and widths, links,
archived link, new-row fill, sort order incl. a property test, platform order, sentence cell,
status words, read text, entity rules, matrix formulas, LibreOffice recalculation).
Updated deliberately: golden corpus and oracle (only a Build/Admin title cue reaches the extractor;
scripted answers for those; the heuristic classes j3 user, j4 working_tool, j12 user, j6 user,
j17 user; claims 14 to 11; extractor first pass 4 to 13), frozen `posting_rubric.yaml` copy (the
loader now requires the `heuristic` block), `test_pipeline` cap test, `test_incremental`,
`test_repo_db` (+ heuristic round trip), `test_spec_v2` jobs parts, `test_evidence_part_c`
(Part C keeps workbook order, now newest first), `test_oracle` + `oracle_expected.json` +
`oracle_account.json` (`careers_host` so its postings are the account's own), `corpus.py`
(posting builders carry pairs, last seen, seniority, archived link, new flag).

## Test commands and results
All with `TEST_DB=ai_test_jobwb PGPASSWORD=dev APP_RW_PASSWORD=... MIGRATOR_DATABASE_URL=...
.venv/bin/pytest <paths> -m "not eval and not live" -q -p no:cacheprovider`.
* `tests/jobs`: 134 passed.
* `tests/exports/test_jobs_workbook.py`: 21 passed (includes the LibreOffice recalculation of the
  Summary matrix and the builder's own `workbook_recalc` check).
* `tests/exports tests/jobs` (one run, 28 min under load): 667 passed, 9 failed. After my fixes of
  the four failures that were mine (Part E and register tests that assumed the old row order and a
  corpus with archived copies; stale generated docs) a re-run of those files passed (207 passed).
  The remaining failures belong to the other agents and were not touched:
  `test_scale` (evidence row count: the Tech agent added a fifth data-pull line, 26,677 rows
  against the pinned formula 26,676), `test_spec_v2::test_ranking_sheet_is_first...`,
  `test_lying::test_b4_detection_without_a_url_is_withheld`,
  `test_people_workbook::test_summary_values_equal_the_planned_counts`; and
  `test_spec_v2::test_people_labels_seniority_lane_and_found_by` /
  `test_oracle::test_produced_workbook_cells` (People wording) failed in earlier runs.
* `ruff check` clean on my files; `mypy src/jobs` clean; `scripts/gen_loops.py --check`,
  `scripts/gen_jobs_docs.py --check` and `python -m exports.docs` current.

## Stays open
1. Nothing writes `entities` / `entity_prefix` into the lock: `datamodel.entity_lock
   .record_entity_lock` replaces the whole JSON each research. The owner's labels and patterns
   need either an operator write that survives it (merge `entities` and `entity_prefix` from the
   old value in that UPDATE) or a settings source. Not touched (not my file).
2. A posting naming a platform only in its title is not a row (the product exports read postings
   naming a platform in the description); the reference includes them with the sentence
   `(named in the title only)`. Needs the title match carried through `is_exported` and the
   pipeline.
3. Rows naming only platforms outside the seven (Snowflake, Power BI, ...) are still rows; the
   reference lists only postings naming one of its seven. Owner decision.
4. `Ref` and `Source code` stay as visible columns right of the reference columns (hide or drop?);
   `Counts` and `Long text` stay after the reference sheets; Full description is not cut at
   32,000 characters (it continues on `Long text`) and markdown marks are not stripped.
5. Status `unknown` (an unrecognised provider word) still prints `Status unknown`; the reference
   has Open or Closed only. Rare after the null-means-open rule.
6. The entity prefix filter is a text prefix: an off-domain label such as `bdx.example (entity to
   confirm)` would count for an account called `BD`. A prefix with a separator would close it.
7. Heuristic classes give no grading claim, so Working tool / User role postings no longer feed
   graded facts; confirm that is wanted.
8. `agents/lanes.py` still matches SAP case-insensitively (its own call of `match_platforms`).
9. Adding the Workday lane adds one live-board search per research.
10. People tests (`assign_priority` import, `found_by` labels) and the Tech ranking test fail for
    the other agents' in-flight changes; not touched.
