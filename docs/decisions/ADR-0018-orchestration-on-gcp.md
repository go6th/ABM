# ADR-0018: orchestration on Google Cloud (decision D-18)

- Status: Proposed (needs the tech lead's sign-off after the spike on the real project)
- Date: 2026-10-03

## Context

Inngest was the outer loop in ARCH v2 (trigger, 240 s slices, approval wait, timeout). On Google
Cloud (ADR-0017) the candidates are Cloud Tasks, Workflows, or Pub/Sub plus a scheduler. The
durability source of truth must stay the LangGraph Postgres checkpoint.

## Decision (proposal)

**Cloud Tasks behind the `RunScheduler` port.** Each slice is one HTTP task to the private worker
service, named `run-<id>-slice-<n>` (a duplicate enqueue is refused by the service and treated as
success), carrying ids only and an OIDC token for a fixed audience. The approval timeout is one
named task scheduled at the deadline. The graph decides everything else; Cloud Tasks only
delivers.

Why not Workflows: the loop logic already lives in the runner and the checkpoint; a second state
machine would be a second source of truth. Why not Pub/Sub: no named, scheduled, de-duplicated
tasks without extra machinery.

## Spike evidence (hermetic; what is proven and what is not)

| Spike requirement | Evidence | Status |
|---|---|---|
| Kill the worker repeatedly; resume without double payment | `tests/system/test_kill_resume.py` (five kills at different stages, SIGKILL of a subprocess worker, resume from the checkpoint) and the connector kill tests | proven hermetically |
| Two workers on one run never both pay | `tests/connectors/test_ledger_budget.py::test_two_workers_on_one_run_never_both_pay`; `tests/system/test_kill_resume.py::test_two_workers_on_one_run_finish_once` | proven hermetically |
| AWAITING_APPROVAL with an approval endpoint | `tests/system/test_whole_run.py::test_approval_gate_waits_then_resumes`; `POST /v1/runs/{id}/approve` | proven hermetically |
| 24 h timeout cancels and refunds | `tests/runs/test_lifecycle_db.py::test_approval_timeout_cancels_and_refunds_once`; `/tasks/approval-timeout` | proven hermetically |
| Named-task de-duplication and OIDC on the real service | port contract test on the Cloud Tasks emulator | emulator only; VERIFY(V-TASKS-1) on the real queue |

## Consequences

- Cloud Tasks' own delivery retries cover the infrastructure failure class only (queue
  `max_attempts = 5`); provider failures are retried by the node RetryPolicy (W6).
- Open: dispatch deadline for a 240 s slice on the real service (VERIFY(V-TASKS-1)).
