# ADR-0039: pause for approval instead of failing at the cap, and the soft limit (D-45, D-46)

- Status: Proposed (D-45 supersedes the D-28 default for unattended runs; D-46)
- Date: 2026-10-06
- Deciders: TODO

## Context

The PRD asks before spending beyond the cap; ARCH ends BUDGET_EXCEEDED at the budget (C14, ADR-0032
chose: attended runs may request one extension, unattended runs stop). The roadmap item asks that a
run that would exceed the cap pauses for approval (notification, one click) instead of failing.

## Decision

* Soft limit: `cap * soft_limit_pct // 100` (integer micro-dollars; proposal 80%). Crossing it is
  recorded once per cap version inside the ledger's own reserve or true-up transaction, notifies,
  and switches funding to priority-only (High rows and units already started).
* A cap denial during the parallel phase stops new paid launches, lets in-flight units finish, then
  opens exactly one pause request per cap version (partial unique index) covering every unit the
  cap blocked; the request (shortfall, suggested cap rounded up to the step, rows at risk, spend
  with bases) is computed in code. The graph routes to `cap_gate`, whose `interrupt` is the one
  owner of the wait; the run is AWAITING_APPROVAL with `awaiting_kind = 'cap'`.
* Approval: a human approver role, the run's tenant from the token, a step-up (MFA) younger than
  the configured age, bound to request id, run, tenant and amount, single use; it appends a cap
  version and re-arms the soft limit. Decline, a timeout (the 24 h wait) or a policy decline (the
  pause count or the ceiling) stop paid work; the run resumes, distils what exists and ends
  BUDGET_EXCEEDED with partial results and one settlement. A late approval is refused and recorded.
* Wallet and cap stay separate (defect 4): the wallet is consulted only when a credit rate is
  configured (D-04 open today); a refusal is a decline.

## Consequences

* Only the parallel execution mode pauses; sequential v1 keeps the D-28 behaviour (its test
  `test_budget_cap_ends_budget_exceeded_with_distill_run` still holds). Switching the default
  execution mode adopts D-45 for every run.
* "One click" is one click after sign-in and step-up; emailed links carry no bearer power.
