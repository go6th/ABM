# Spec compliance: code on `feat/integrate-gcp-production` against `docs/prompts/`

Pass 1, written 2026-10-02 after the owner supplied the workflow, tracker and job pipeline
spec prompts. Each line is checked against code that was read, not memory. "Fixed in" names
the commit or the work item that closes the gap; an empty cell means still open.

Status words: **Met** (code and a test exist), **Partial** (code exists, the guarantee is not
fully enforced or not tested), **Missing**, **Defect** (code exists and is wrong).

## Cross-cutting defects found while checking

| ID | Finding | Evidence | Fixed in |
|---|---|---|---|
| SC-1 | Two key spaces for the same 33 rows. `config/checklist.yaml` uses ids `P1.01`..`P2.28`; the skill's canonical `assets/checklist.yaml` uses keys (`prior_customer_status`...). Labels match row for row. The workflow owns `P` ids, the tracker seeds skill keys, and the specs name rows by key (`job_board_by_lane`, `lead_verification`), so no stage could ever report a row the tracker seeded. | `workflow_v1.yaml` vs `tracker/config.py` | SC-1 commit (this one) |
| SC-2 | The tracker reads `row["key"]` from `config/checklist.yaml` in places and from the skill asset (cwd-relative path) in others; priorities in the config copy are `High`, the DB CHECK wants `high`. | `tracker/config.py`, `tracker/service.py` | tracker rebuild commit |
| SC-3 | `coverage_row_events.from_status` is read by a sub-select **after** the row UPDATE, so every event records `from = to`. Also: `app_rw` had no USAGE on the events id sequence, so no event could be written at all. | `tracker/repo.py` `update_row`, migration 0008 | tracker rebuild commit, migration 0011 |
| SC-5 | Tracker logic defects: a judge or gate could upgrade `not_done` to `done` with new proof; an unknown result count counted as zero (could yield None); an unseeded run evaluated as completable; a swept row could later be upgraded and its swept flag cleared. | `tracker/machine.py`, `derive.py`, `completion.py`, `repo.py` | tracker rebuild commit |
| SC-4 | A second coverage implementation (`tracker/legacy_coverage.py`, re-exported by `workflow/coverage.py`) still validates statuses supplied by the caller (T1 forbids it). | `workflow/stage.py` imports it | |

## Tracker state machine (`build_tracker_state_machine.md`)

Rebuilt after this check; the table shows the state after the rebuild commit.

| Section | Requirement | Status | Evidence |
|---|---|---|---|
| 0.3 | Exactly five statuses, unset = NULL | Met | DB CHECK, `Status` enum |
| 2.1 | 33 rows, 5/28, 19/7/7 asserted from the file | Met | `test_tables.py::test_checklist_counts_are_read_not_typed` |
| 2.2 | D-12 and D-13 proposals in config, ADR, register | Met | ADR-0015, `open-decisions.md` |
| 4 | Module layout, generated transitions doc | Met | `scripts/gen_tracker_docs.py --check` test |
| T1 | No status parameter on `record_outcome` | Met | `test_t1_*` |
| T2 | Proof read from the ledger; foreign refs refused | Met | `LedgerCallAdapter`; `test_t2_*` (4 tests) |
| T3 | Unknown count never Done or None | Met | `test_t3_*`, property test |
| T4 | None needs zero results or a verified lane switch-off | Met | `test_t4_*`, `test_lane_switched_off_*` |
| T5 | `expected_calls` | Met | floor from config (default 1), stage may declare more, never fewer |
| T6 | Blocked needs a failure; Not done cannot hide one | Met | `test_t6_*` (2 tests) |
| T7 | Ownership and actors | Met | owner pinned at seeding; `test_t7_*` (4 tests) |
| T8 | Cumulative proof | Met | `test_t8_*` |
| T9 | Detail internal, sanitised, scanned | Met | `test_t9_*` (4 cases) |
| T10 | Allow-list client view, scanned | Met | `test_client_view_is_allow_listed_scanned_and_ordered` |
| T11 | Tally recomputed, bases named | Met | `tally.py`, view test |
| T12 | Sweep blocks COMPLETED; swept rows frozen | Met | `test_t12_*`, DB guard tests |
| T13 | Lead rule via the grading detector | Met | `test_leads_delegation.py` |
| T14 | Re-derive and downgrade on claim drops, never to None | Met | `test_dropped_claims_*`, `test_t14_*` |
| 5.1 | Seed in the same transaction as the run | Partial | `seed()` runs on the caller's transaction; the run-creation path that calls it is the run lifecycle work item |
| 5.5 | `guard.finish_run` only path to COMPLETED; both run tables | Met | `test_guard_*` |
| 6 | Expand-only migration, grants, CHECKs, trigger as `app_rw` | Met | migration 0011; `test_db_refuses_*` (3 tests) |
| 6.5 | SQL mirror of the table | Not built (optional) | ADR-0015 item 7 |
| 9.4 | Stateful model test | Met | `test_stateful.py` |
| 9.6 | Concurrency and SIGKILL | Met | `test_racing_*`, `test_twenty_*`, `test_sigkill_*` |
| Coverage | 90% overall, 100% on machine, derive, completion, guard, model | Met | tracker run: those five at 100%, service 97% |

## Workflow v1 (`build_workflow_v1.md`)

| Section | Requirement | Status | Note |
|---|---|---|---|
| 0.2 | Inngest only triggers | Superseded | the integration prompt moves orchestration to Cloud Tasks behind `RunScheduler` (D-18, conflict row C11) |
| 2.2 | Every checklist row has exactly one owner | Defect | see SC-1 |
| 2.3 | Verification before distill; judge after distill; ADR | Partial | ADR-0005 records it; no `judge_verify` code |
| 2.4 | Versioned prompts under `src/agents/prompts/`, hash on the run | Missing | |
| 3 | Evidence gate rules 1 to 10 | Partial | quote, URL, figure, Confirmed, question rules exist in `evidence_gate.py` and `grading/`; narrative check (3.6), re-ask (3.7), sensitive flag (3.10) missing |
| 4 | `budget_view.py`, `runner.py`, `finalize.py`, `stages/s01`..`s13` | Missing | only `s05_jobs` exists and it raises `NotConfigured` |
| 4 | Small state with reducers | Met | `state.py` |
| 5 | Thirteen stages | Missing | |
| 6 | Loop exits recorded | Partial | `loop_exits` key exists; nothing writes it |
| 7.1 | Run transitions with optimistic UPDATE | Partial | pure table in `transitions.py`; DB constraint and trigger added in 0010; no repository |
| 7.2 | Resume, version pin, `migrate()` | Partial | `migrate()` exists |
| 7.4 | `finalize` exactly one settlement | Partial | unique indexes in 0010; no code |
| 8 | FakeLlm, synthetic accounts, whole-run, resume matrix | Missing | |

## Job posting pipeline (`build_job_posting_pipeline.md`)

Rebuilt after this check; the table shows the state after the rebuild commit. Defects the
hand-written oracle caught during the rebuild: a strict Enum made every real extractor answer
schema-invalid; out-of-window postings were still classified and claimed; a live-board hit and
its feed record never merged; a dot inside a word ended a sentence.

| Section | Requirement | Status | Evidence |
|---|---|---|---|
| 2.2 | Lexicon from PRD names only; every entry sourced | Met | `test_every_config_entry_carries_a_source_or_status` |
| 2.3 | Rubric rules with positive and negative examples | Met | `test_every_rubric_rule_has_a_passing_positive_and_negative_example` |
| 2.4 | D-16 default superset; D-17 proposal | Met | ADR-0008, register |
| J1 | Counts from rows, invariants, no count parameters | Met | `counts.py`; property tests; `test_no_function_in_counts_accepts_a_count` |
| J2 | Read means complete text this run | Met | `test_minimum_text_length_makes_short_text_unread`, golden |
| J3 | Verbatim sentence per named platform | Met | `test_platform_sentences_are_verbatim_spans_of_the_stored_text` |
| J4 | Rules first; extractor validated; second pass for build and admin_owner | Met | `test_lying_model.py` (12 cases) |
| J5 | One bounded escalation per posting, run cap | Met | `test_caps_bound_escalation_and_extractor_calls` |
| J6 | Window from `as_of` to the day | Met | `test_calendar_window_boundaries_to_the_day`, property |
| J7 | Live board beats feed status; unknown stays unknown | Met | golden `status` |
| J8 | Dedupe by id or URL, never title | Met | `test_dedupe_*` |
| J9 | Not searched is never zero | Met | `test_all_lanes_off_is_not_searched_never_zero`, golden |
| J10 | URL and provenance on every row; no-URL rejected and counted | Met | golden `rejected_no_url`, DB CHECK |
| J11 | Claims templated to the grading schema | Met | `ProposedSourced`; golden claim count |
| J12 | Untrusted text: hostile excluded and counted | Met | golden (`j15`, `j16`) |
| J13 | No constructed URLs | Met | `test_no_url_is_constructed_from_a_domain` |
| J14 | Pull once; replay makes zero calls | Met | `test_rerun_makes_zero_paid_calls_*`, DB round trip |
| J15 | Export scanned; no provider code or refs | Met | `test_export_and_count_sentence_are_client_safe` |
| 6 | Expand-only storage, append-only, RLS | Met | migration 0012; `test_repo_db.py` |
| 7 | Gap re-entry processes only the remainder | Partial | re-entry replays the stored record; a remainder-only gap pass is not built (open item OI-JOBS-3) |
| 8.1 | Read row None when nothing matches | Deviation | OI-JOBS-1 (tracker derives from ledger counts) |
| 9.7 | Resume matrix with kills | Partial | idempotent replay tested; subprocess kill matrix is in the system tests work item |
| 9.11 | Mutation check | Pending | runs with the gates |
| Coverage | 100% on window, counts, dedupe, match, sentences, classify, extractor | Met | job run: all at 100% (sentences 97% branch) |

## Connectors and grading

Delivered on their own branches with their own gate runs (`docs/prompts/build_broker_connectors.md`,
`build_grading_engine.md`); the integration audit (`AUDIT.md`, findings F-4, F-5) records that
neither is called by the runtime. Their open items stay in `docs/providers/CLAIMS.md` and the
grading report.
