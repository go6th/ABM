# People List workbook and people classification rules: done (2026-10-29)

*Built against the owner's reference People List (reference set of 2026-10-20, measured
2026-10-29: `docs/parity/reference_spec.md`, `docs/parity/reference_manifest.json`, audit findings
P1 to P8 in `docs/audit/2026-10-29-output-parity-audit.md`). The reference account's name and the
names of its people appear nowhere in code, config, fixtures or tests; every test person is
synthetic. Nothing was committed, no server was started, no research run and no paid pull was
made.*

## 1. What changed

### Rules (the people module, `src/people/`, `config/people_rubric.yaml`)

| Audit | Change | Measured on the reference's 736 titles (product rules, read from the reference file only for measuring) |
| --- | --- | --- |
| P1 | A title with no level cue is the default level `other` ("Lead, owner, specialist or developer"), not `review(level_unknown)`; only a person with no title at all still goes to review. A person below the seniority rule (levels `manager`, `other`, band `below`) stays on the list with `Meets seniority rule` = No when the TITLE (not the headline) names a platform or technology (`people_stack_terms` in the rubric plus the lane lexicon's platform names and aliases); otherwise removed and counted (`out_of_scope(below_scope)`, named in the Summary note with its reason). | all 736 stay in scope (was 371); Meets seniority rule agrees on 735 (the one difference is a title with a double space that the reference's own pattern missed) |
| P2 | Levels `senior_director` and `associate_director` added; cues extended to the reference tiers (sr, sr., senior manager, mgr, dir, cio/cto/ceo/cfo, global lead, executive director). `level_order` as the reference's Level table. A cue inside a longer cue's match is set aside (longest cue wins), then the most senior level wins, so "associate director" is Associate Director and "senior vice president" is SVP. `level_bands` cover the new levels and a `below` band. | Level agrees on 734 of 736 (the other two are the reference's double-space titles) |
| P3 | ONE lane per person from the eight role-family rules, ordered, first hit wins, default "Business leadership and other" (`people_lanes` in the rubric, `role_lane` stored in `people.classification_json`, so no column was needed). The platform lanes stay as they were for the gap rule and the association search. The Summary Lane table has eight rows. | Lane agrees on 734 of 736 (was 78) |
| P4 | Priority from the title alone: C overrides (executive assistant, emeritus, retired), C for a non-IT function unless the title names a platform, B for M&A and business integration, A for platform names and IT leadership, default B. No named-person fix anywhere. | Priority agrees on 718 of 736 (97.6 percent; was 207 of 371). The 18 differences are the reference's own inconsistencies (the same phrase rated A in one row and B in another). |
| P7 | Exclusion cues `cyber`, `cybersecurity`, `ciso`, `internal audit`, `privacy officer` added to the rubric and `config/people_scope.yaml` (the loader demands equality) and to the frozen fixture copies. `config/skill/scope.yaml people_exclusions` is NOT read by the people pipeline and cannot be wired without the people module importing the agents' config: the new terms were added to it and a comment says so. | |

Other rule changes that follow from listing people below the rule: no profile pull for them (profile
targets are the people who meet the rule), no title claim for them, they do not cover a gap lane,
and the Summary's Key people table skips them.

### Found by, former employers (P5, P6)

* `PersonRow` carries `role_lane` and `found_via` (search kind, title-search pass, association
  platform lane, research page). The cell prints the reference strings: `title search source 1
  (leadership and platform)`, `title search source 2 (manager)`, `technology association search
  (<platforms>)`, `Leadership page; profile found by search` (any other page: `Research page;
  profile found by search`), several joined by `; ` (templates in `labels.found_by`).
* 28 SI and consulting firms with alias patterns in the rubric (`si_firms`). A profile now carries
  dated `employments` (employer, start, end); the cell prints `Firm (2015 to 2019); Firm (2020 to
  date)`. With no profile data the cell is empty (it no longer says "Not pulled").
* **The production profile port is still `UnavailableProfiles`; no paid profile pull was enabled.**
  Until the owner decides to wire one (it costs credits per person), the cell stays empty in
  production. A comment at the wiring says so.

### The workbook (P8)

* People sheet: the reference's 13 columns in order with its widths (6, 26, 56, 22, 11, 30, 9, 30,
  30, 13, 13, 24, 50), then the product's own `Ref` and `Source` (the row key the row-conservation
  check and the Summary formulas count, and the source register code). Header height 32, freeze
  `C2`, Priority cell filled A `DDEFE0` / B `FFF1CC` / C `EEEEEE` and centered, `LinkedIn` and
  `Second profile` read `Open profile` with the address behind them, rows sorted level, priority,
  lane, name. The "Also in lane" note is gone (one lane per person).
* Summary sheet: title, SIX notes at rows 3 to 8 (merged A:F, 58 points: what the list merges with
  each search's own counts, the rows removed, the people excluded by title, the seniority rule with
  the sentence on people below it, the judgment note, the entity labels), Level table header at row
  11, levels 12 to 20, Total 21, Meets seniority rule 22, Lane header 24, eight lanes 25 to 32,
  Total 33. Every count is a formula citing its label cell (`=COUNTIF(People!$D$2:$D$N,A12)`);
  the two Total rows are bold; column A is 44 wide and B to F 12. The count sentences after the
  Lane table are no longer printed (they stay on the model for the Word Summary).

## 2. Files

Config: `config/people_rubric.yaml`, `config/people_scope.yaml`, `config/skill/scope.yaml`
(people_exclusions, seniority rule wording "Director and above, Head, and Senior Manager"),
`config/export_rules.yaml` (`labels.people_*`, `labels.found_by*`, `sheet_texts.people`,
`workbooks.people`).

People module: `src/people/cues.py`, `config.py`, `level.py`, `lane.py`, `priority.py`, `scope.py`,
`models.py`, `profiles.py`, `si_firms.py` (new), `export.py`, `repo.py`, `erasure.py`, `gaps.py`,
`claims.py`, `pipeline.py`, `docs.py`; `docs/people/rubric.md` regenerated.

Exports: `src/exports/excel/people.py`, `src/exports/readers/people.py`,
`src/exports/readers/pg.py` (the `_people` reader only), `src/exports/content.py` (`PersonRow`,
`FoundVia`), `src/exports/config.py` (small additions: `Labels` and `PeopleSheetTexts` fields, the
`PeopleBook` layout fields), `src/exports/readers/summary.py` (one line: Key people skips a person
below the rule), `src/account_intelligence/db/enums.py` (`PersonLevel` gains `senior_director` and
`associate_director`), `src/agents/production/pipelines.py` (a comment only).

Migration: `migrations/0089_people_fields.sql`, `.down.sql`, `migrations/versions/0089_people_fields.py`
(down_revision `0088`): `person_profiles.employments jsonb`, the erased-profile check and the
erasure trigger extended over it, and the `people.job_level` check widened to the new levels and
`other`. `EXPECTED_REVISION` was already `0090` (the Tech agent's), so it was not touched.

Tests: `tests/people/*` (pure rules, rubric, golden oracle and its fixtures, erasure lists, repo
round trip and checks, `test_si_firms.py` new), `tests/exports/test_people_workbook.py` (new, with
the manifest test), `tests/exports/test_spec_v2.py` and `test_oracle.py` (people parts),
`tests/fixtures/synthetic/people/oracle.yaml`, `tests/fixtures/synthetic/jobs/config/people_*.yaml`
(frozen copies, now equal to the production files).

## 3. Test commands and results

Every run used `TEST_DB=ai_test_peoplewb` with the private scratch database, `PGPASSWORD=dev`,
`APP_RW_PASSWORD` read from `.env` by `grep` (never sourced) and
`MIGRATOR_DATABASE_URL=postgresql://postgres:dev@localhost:5432/ai_test_peoplewb`, and
`-m "not eval and not live" -q -p no:cacheprovider`.

| Command (paths) | Result |
| --- | --- |
| `pytest tests/people tests/exports` (final, whole) | `3 failed, 926 passed, 7 warnings, 1 error in 980.74s`: every failure and the error belong to the Jobs and Tech work (see below), none to People |
| `pytest tests/people tests/unit/test_enums.py tests/exports/test_people_workbook.py tests/exports/test_summary_people.py tests/exports/test_oracle.py tests/exports/test_static_guards.py` | `558 passed in 50.92s` |
| `pytest tests/unit tests/phase3/test_ws1_tenancy.py` | `284 passed in 38.41s` |
| `pytest tests/documents/test_deliverables_fields.py tests/replica/test_sheet_cells.py tests/agents/test_units.py tests/exports/test_properties.py` | `51 passed in 32.03s` |

Not People: `tests/exports/test_lying.py::test_b4_detection_without_a_url_is_withheld` (the stack rows
of the Tech workbook), `tests/exports/test_scale.py::test_large_account_builds_within_the_memory_cap`
(the Evidence part H line count), `tests/exports/test_spec_v2.py::test_ranking_sheet_is_first_and_its_formulas_are_registered`
(the Platform teams note), and the error `tests/exports/test_jobs_workbook.py::test_rows_are_always_newest_first_and_conserved`
(`fixture 'seen' not found`, a hypothesis test written with a function parameter). I did not
touch any of them.

Gates: `python -m exports.docs` (docs/exports regenerated; the file also carries the other agents'
pending columns), `python -m people.docs --check` current (`docs/people/rubric.md` regenerated),
`python scripts/gen_loops.py --check` current after regenerating (line numbers moved),
`tests/unit/test_enums.py` passes (`PersonLevel` gained two members), `scripts/abm_name_scan.py`
no new hits, `scripts/check_comments.py` OK, `ruff check` and `mypy` clean on the People files.
The isolation matrix and ownership files list tables, not columns, and no table was added.

New tests: `tests/exports/test_people_workbook.py` (manifest test over
`docs/parity/reference_manifest.json`, widths, freeze pane, header height, fills, link texts,
sorting, Summary rows 11 to 33 and their formulas, six paragraphs, Found by, former employers, one
lane, and an end-to-end build from the real people pipeline through Postgres),
`tests/people/test_si_firms.py` (28 firms, dated format, parsing), the level, lane, priority,
exclusion and below-rule cases in `tests/people/test_pure.py`, and the DB cases (employments round
trip and erasure, the widened level check) in `tests/people/test_repo_db.py`.

## 4. What stays open

1. **Profile pull (owner decision, costs money).** Former SI and consulting employers stay empty in
   production until a profile pull is wired; the parsing, storage, firm matching and printing are
   done and tested.
2. **Bare `audit` and `compliance`** still go to review (`exclusion_ambiguous`); the reference
   excluded every `audit` title. Audit item 6 stays an owner decision.
3. **Search words for the new levels.** `level_words` has no entry for Senior Director and Associate
   Director, so the title searches still ask for "Director" and "Senior Manager" only. Adding the
   two words adds title strings to the paid searches; not changed without the owner.
4. **Priority is 97.6 percent, not 100.** The reference rates the same phrases A and B in different
   rows; the rubric follows the written rule.
5. **Note column.** The reference notes the employer label of a row ("Employer label on the record
   ...") and profile remarks; the product leaves the column empty (no source for either).
6. **Entity paragraph** names the subsidiaries of the stored entity lock; the divested-business
   sentence is generic. Per-account labels and dates arrive with the Jobs agent's entity rules.
7. The Priority fill and Level counts are verified after recalculation only where
   `EXPORT_RENDER_CHECKS` runs (audit Q1: not set on staging).
