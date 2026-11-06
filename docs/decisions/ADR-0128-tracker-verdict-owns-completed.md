# ADR-0128: the tracker verdict is the one owner of "Completed"

- Status: Accepted (code)
- Date: 2026-11-03
- Deciders: owner (fix prompt E, task 1)

## Context

Two rules decided whether a research run ended COMPLETED:

1. the research graph's deliver step (`agents.graph`, `deliver`) computed
   `complete = not withheld and not broken and every row Done/None or a stated gap` and returned
   `outcome: completed | partial`;
2. `runs.lifecycle.finish` evaluated `tracker.verdict` and demoted a COMPLETED request to PARTIAL,
   then `tracker.guard.finish_run` evaluated the verdict **again** and demoted again (with a JSON
   `{"blocked_by": [...]}` as `finished_reason`).

The rules disagreed: the graph judged every row (any priority) by status, the tracker judges High
rows under the run's pinned policy plus hard blocks (unset, swept, no proof, short tracker). A run
the tracker could complete ended PARTIAL `steps_waiting` because of a Medium row; a run the graph
called complete was demoted with an unreadable JSON reason.

## Decision

- The deliver step releases the files and returns nothing. It reports **facts**, never a vote:
  the files the gate held back reach `AbmGraphFactory.target` as `outcome=partial,
  error_code=files_withheld` (set by the gate step, unchanged), a failed pipeline as the
  `PIPELINE_FAILURES` counters (mapped to PARTIAL `steps_waiting` in `target`, unchanged).
- `runs.lifecycle.finish` calls `tracker.verdict` exactly once, before the sweep, and hands it to
  `tracker.guard.finish_run(verdict=...)`. `guard.terminal_status(target, verdict)` is the one
  mapping: COMPLETED only when `verdict.can_complete`, otherwise PARTIAL with the reason code
  `tracker_verdict` and the blocking rows in `stop_detail` (`blocked_by: k1, k2, ... and N more`).
  The demotion branch in `lifecycle.finish` is gone; `finish_run` called without a verdict (tests,
  direct callers) still evaluates it itself, once.
- `tracker_verdict` is a "finished with gaps" reason (like `steps_waiting`): it has client text
  in `replica.workspace.STOP_TEXT` and is in `_FINISHED_REASONS` (no Resume offered).

## Consequences

- A run whose only gaps are non-High rows (or High rows with an allowed stated-gap reason) now ends
  COMPLETED (level `complete_with_gaps`) instead of PARTIAL `steps_waiting`. This is the intended
  single policy (`config/tracker_v1.yaml`, PRD quality metric), but it is a visible change.
- FIX_LOG decision D1 (the replica kept the graph's partial-on-open-rows rule) is superseded; the
  source guard `test_the_abm_deliver_step_still_reports_partial_when_rows_are_open` became
  `test_the_abm_deliver_step_never_votes_on_the_terminal_status`.
- Tests: `tests/runs/test_lifecycle_db.py::test_the_graph_says_complete_the_tracker_says_not_the_run_ends_partial`
  (PARTIAL, `tracker_verdict`, the row in the detail, verdict evaluated once);
  `tests/agents/test_orchestration_fixes.py::test_the_graph_never_demotes_a_run_the_tracker_may_complete`
  (the graph asks COMPLETED with blocked rows, so the reverse case cannot occur);
  `tests/tracker/test_service_db.py::test_guard_never_completes_with_gaps_and_the_db_refuses_it_directly`
  (reason code instead of JSON).
