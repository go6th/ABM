# ADR-0037: units of work, key-ordered merge and source-code allocation (D-42)

- Status: Proposed (D-42, needs product owner sign-off)
- Date: 2026-10-06
- Deciders: TODO

## Context

Parallel execution must not make outputs depend on which unit finished first (P1). LangGraph `Send`
fan-out was considered: in langgraph 1.2.12 (verified with `InMemorySaver`, see
`docs/open-items.md` V-LG-1) completed sibling tasks' writes survive a cancelled superstep and only
the unfinished task re-runs on resume. A superstep is still a barrier: a ready unit waits for the
slowest sibling of its batch, and per-unit launch control (provider slots, funding, deadline,
pause) does not fit a single `Send` batch.

## Decision

* A work unit is `(stage, lane, source type, shard)` with a deterministic key
  (`<type>|<lane>|<shard>`, or `body|<stage>` for today's stage-granular bodies).
* One graph node (`research`) runs the units on an async executor; the durable truth for units is
  the insert-only `work_unit_events` table (plus the small result map stored with each terminal
  event), and every iteration re-derives the ready set from it (P9). The LangGraph checkpoint stays
  the truth for the graph position (rule 8).
* Unit results are merged by unit key with the checkpoint state's own reducers; a unit's input is
  the phase input plus its transitive dependencies' results only.
* Source codes: `parallel.merge.allocate_in_key_order` allocates in key order. Today only the people
  body allocates codes inside its own code (profile URLs); bodies flagged `allocates_sources` are
  chained in key order by the planner so allocations never interleave. A fine-grained unit that
  cites URLs must return them for the merge to allocate.

## Consequences

* Proven: identical merged updates under 50 random completion orders
  (`test_executor_outputs_are_identical_under_random_completion_orders`); parallel and sequential
  whole runs end with the same 33 row statuses (`test_parallel_whole_run_completes_with_the_same_rows_as_sequential`).
* Today's bodies are stage-granular (the pipelines take no lane or person scope), so the engine
  runs the coarsened DAG; per-lane and per-person units exist in the planner and the simulation and
  become executable when a stage accepts a unit scope.
