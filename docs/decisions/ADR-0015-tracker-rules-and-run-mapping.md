# ADR-0015: tracker transition, derivation and completion rules; run-table mapping

- Status: Proposed (D-12 and D-13 need the product owner's decision)
- Date: 2026-10-02
- Deciders: TODO (product owner)

## Context

The tracker spec (`docs/tracker/transitions.md`) gives the rule (every row ends
Done, Partial, Not done, Blocked or None with proof; no COMPLETED while a High row lacks a status)
but not the machinery. The spec-compliance check (`docs/audit/SPEC-COMPLIANCE.md`) found the first
implementation let a judge or gate upgrade a row, treated an unknown result count as zero, let
any stage write any row, allowed an unseeded run to evaluate as completable, recorded every event
with `from = to`, could not write events as `app_rw` (missing sequence grant), and let a swept row
be upgraded later.

## Decision

All of the following are **proposals** (`status: proposal` in `config/tracker_v1.yaml`):

1. **D-13 transition table**: the spec's 5.2 table, unchanged, with actor rules applied before any
   cell: a stage must own the row (`owner_stage` pinned at seeding from `config/workflow_v1.yaml`);
   the verifier only the rows in `verifier_rows` (`lead_verification`); judge and gate only the
   downgrade cells; the finalizer only the unset to Not done sweep with a `swept_*` reason. A swept
   row never changes again.
2. **D-13 derivation**: the spec's 5.3 rules, plus: a successful call with an unknown result count
   never supports Done or None (T3) and yields Partial `result_count_unknown`; a Blocked reason is
   the most severe failed call's error class mapped through `error_reason_map`.
   `waiting_on_account_owner` is **not** an out-of-scope reason: a relationship row with no
   pursuing client is Not done with that reason (nobody searched), never None.
3. **D-12 completion policy**: default `high_rows_allowed_for_completed: [done, none]`, pinned per
   run in `run_coverage_meta`. Hard blocks (unseeded, short, unset, swept, status without proof)
   are not policy.
4. **Proof** is a copy of ledger records (category, queries, success, result count, error class),
   hashed canonically without timestamps or notes; gap-loop progress counts proof calls, not hash
   changes (audit F-12).
5. **Reason codes added** beyond the spec's list (5.4): `cap_reached`, `source_failed`,
   `fewer_than_expected`, `out_of_scope`, `result_count_unknown`, `not_planned` (the planner chose
   no call for the row; Not done, never a failure). Each has client text that passes
   the client-output denylist (tested).
6. **Run-table mapping.** `account_runs` is the orchestration run; `runs` is the data-layer row with
   the same id (created together, see the run lifecycle). `runs.status` cannot be widened
   expand-only, so `tracker.guard.finish_run` maps COMPLETED to `completed` and every other
   terminal state to `failed` with `error` naming the state and reason. A contract migration that
   widens `runs.status` is proposed for after the expand window.
7. **SQL mirror of the table** (spec 6.5, optional): not built. The pure table is enforced in code
   and tested cell by cell; the database enforces the completion rule (migration 0010).

## Consequences

- Rejected: letting the service union old references automatically (it would make a swapped proof
  impossible to detect); counting a changed hash as progress.
- Open item OI-TRK-1: "rows holding Leads" for the verifier beyond `lead_verification` needs the
  Lead-to-row mapping from the grading layer.
- Evidence: `tests/tracker/test_tables.py` (oracle), `test_lying_stages.py` (T-rules),
  `test_service_db.py` (database defences, races, SIGKILL), `test_stateful.py` (model).
