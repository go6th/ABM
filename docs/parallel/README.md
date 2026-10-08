# Parallel execution (Parallel execution v1)

Status: built and tested hermetically and on PostgreSQL 16; **opt-in** (`execution_mode: parallel`
in `config/workflow_v1.yaml`; the default stays `sequential` until D-42 to D-47 are signed). Spec:
`docs/spend/credit-manager-v2.md`. Decisions: ADR-0037 (units and merge), ADR-0038
(governor), ADR-0039 (pause instead of fail), D-44 (`VALIDATION.md`).

## Dependency graph (`config/parallel.yaml`)

Every edge is data, validated at startup (acyclic, every dependency known, every unit type covered
by exactly one body) and pinned by `test_every_prd_edge_exists_and_no_unsupported_edge`.

| Unit type | Fan-out | Depends on | Basis |
| --- | --- | --- | --- |
| pass1_relationship, baseline, news_pull, postings_12m, tech_detections, review_communities | once | none (roots) | PRD 4.2 steps 2 to 8 |
| jobs_board | per in-scope lane | none | PRD 4.2 step 5 |
| title_search | per lane and seniority pass | none | PRD 4.2 step 7 |
| people_merge | once | title_search | step 7: merge by profile URL, then name |
| association_search | once | people_merge | roadmap: after the first merged pass |
| profile_pull | per senior person (stored rows, cap 50) | people_merge | step 7 |
| people_levels | once | profile_pull, association_search | step 7 |
| posts_priority_a | per priority A person (cap 25) | people_levels | step 7: posts only for priority A |
| name_search | per top-ten person and category | people_merge | SaaS 4.2 step 4 |
| platform_sentences | once | postings_12m | step 5 |
| tech_cross_check | once | postings_12m, tech_detections | step 6 |
| lead_verification | once | every pull (soft) | step 9 |

Then distill, build, gate and deliver run in order (the graph's sequential tail).

**Bodies.** Today's stage handlers are stage-granular, so each stage is one composite unit over
the coarsened graph: pass 1, baseline, news, jobs and people start together; technology waits for
jobs (the postings); person-name searches wait for people; verification waits for everything,
softly. Per-lane and per-person units exist in the planner and the simulation and become executable
when a pipeline accepts a unit scope (`granularity: unit`).

## Scheduler (`src/parallel/scheduler.py`, `executor.py`)

`next_batch` is pure: dependencies (a failed hard dependency blocks with `dependency_failed`),
funding (`priority_only`, `paused`, `stopped`), provider holds, the deadline (a unit whose p95 would
end inside the tail reserve is blocked `deadline`; an unmeasured p95 uses the flagged fallback),
then priority, remaining critical path, age and key, within the run's slots. The executor
re-derives states from `work_unit_events` every iteration, runs at most `max_in_flight_units`
bodies, records typed outcomes, and exits `converged`, `deadline`, `budget`, `paused`,
`cancelled`, `retryable` (re-raised for the node RetryPolicy) or raises `SchedulerDeadlock`.

## Governor (`src/parallel/governor.py`, migration 0017)

Leases per provider across all runs and workers, with run and tenant shares, fewest-held tenant
first, aged priority, TTL reclaim and AIMD. The connector holds a slot only around a live call that
is not already in the ledger. See ADR-0038 and the runbooks below.

## Simulation (`src/parallel/simulate.py`, `scripts/simulate_parallel.py`)

`simulation-report.md` is generated from a fixed synthetic scenario. It is a simulation: it says so
in every section and claims nothing about real providers or the 4 hour target. Note the effect of
the proposed 50% run share: a single run can hold at most half of a provider's slots.

## Runbooks

`runbook-stuck-lease.md`, `runbook-deadlock.md`, `runbook-limit-storm.md`.
