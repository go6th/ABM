# Reconciliation: the skill (as the guide describes it) against the platform's config

Nothing was changed silently. Each difference is a conflict-register entry (C30 to C34) and, where
a choice is needed, an open decision.

| # | Topic | Skill (guide) | Platform today | Resolution now | Register |
|---|---|---|---|---|---|
| 1 | Checklist rows, priorities, cadences, gate requirements | checklist.md sections 1-7, 23 KB (S7); exact rows unknown | `config/checklist.yaml`: 33 rows from PRD Appendix A, 11 shelf lives | **Not reconcilable without the archive.** The 33 platform rows drive the pull plan and tracker. | C31 (open) |
| 2 | Lanes | Description lists Salesforce, ServiceNow, SAP, Tableau, Veeva, Data and AI (S13) | `config/lanes.yaml`: the same six plus `other` | Match (the platform's `other` is an addition, kept) | none |
| 3 | MuleSoft | Counted under Salesforce, searched on every account; not named in the description (S13, S14) | `lanes.yaml` Salesforce label names ServiceMax, not MuleSoft | Named in `config/skill/run.yaml` and the Pass 2 prompt. The client-facing lane label is **not** changed (it changes the export rules hash and documents): owner decision | C30, D-100 |
| 4 | Three phrasings | "trying three phrasings before dropping a lead" (S5); tool-count wording unknown | Platform rule 4: "three differently worded queries across two tools" | Stricter applies: 3 phrasings across 2 tool categories, until the archive's wording is read | C32 |
| 5 | Operator-only file | Named after a person (S11, S13) | `output_spec.yaml`: `{account}_Operator_Only`, a comment naming the person | Renamed `{account}_Internal_Only`; comment reworded; the name is held only as a digest and `scripts/abm_name_scan.py` fails on it | C33 |
| 6 | Phase split | Phase 1 pulls everything, Phase 2 interprets after a sense check (S9, S10) | `config/workflow_v1.yaml` interleaves pulls and interpretation per stage | The ABM graph runs under the production runner for Phase 1 only and Phase 2 only runs, and for full runs behind the `abm_agents` tenant flag; making it the default is the owner's decision | C34 (open), OI-ABM-1 |
| 7 | Grading scheme | output-spec.md grading scheme (S11), unknown | `config/grading.yaml` (D-01 proposal) | Platform grading engine stays the only grader | none (archive needed) |
| 8 | Pre-delivery gate | checklist.md gate (S7), unknown | `config/output_spec.yaml` gate checks | Platform gate runs; archive gate items unreconciled | C31 |

## Found while binding to the production platform (C35 to C39)

| # | Topic | Resolution | Register |
|---|---|---|---|
| 9 | Phase 2 grading and tracking need the pull's ledger | Declared parent's ledger presented as the child's inputs | C35 |
| 10 | Search snippet versus keyword match | Engine rule `unread_match`; snippets graded by evidence, never Confirmed unread | C36 |
| 11 | Phase 1 only has no tracker verdict, every run ends terminal | PARTIAL `pull_only`, rows swept visibly | C37 |
| 12 | Grading attribute classes cover four classes only | Map rows to existing classes; others refused and counted; **owner** | C38 |
| 13 | G8 "Blocked" versus tracker T1 (Blocked needs a failed call) | Tracker says Not done `consent_required`; manifest says Blocked | C39 |

## Pre-existing findings met during this package

* `scripts/lint_migrations.py migrations` already fails with 13 findings on tables of earlier
  packages (0036 to 0040: `unit_cost_rollups`, `run_anomaly_events`, `link_checks` and others have
  no FORCE RLS or policy). Migration 0041 adds none (identical output with and without it).
* Test fixtures written before migration 0033 planted a hypothesis fact without a confirming
  question and a verified careers URL without its fetch reference; the isolation matrix and three
  grading tests failed before reaching their own checks. The fixtures now supply both (the
  constraints are unchanged).
* `ruff check .` reports 26 findings, all in files of earlier packages
  (`tests/crosscut/test_x4_privacy.py`, `src/privacy/dsar.py` and others); none in this package.
