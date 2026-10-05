# Runbook: operating runs

- A run that seems stuck: `GET /v1/runs/{id}` shows status and coverage. A worker that died leaves
  the run lease until it expires (300 s); Cloud Tasks redelivers the slice, which then resumes from
  the checkpoint (paid calls replay from the ledger).
- A run past 60 slices ends FAILED with a full refund (slice ceiling).
- AWAITING_APPROVAL runs expire after 24 h: canceled and refunded by the approval-timeout task.
- Provider outage: affected rows end Blocked with a reason and the run continues; the circuit
  breaker opens per provider. Check provider status, not the run.
- Budget: a run never exceeds its cap; hitting it ends BUDGET_EXCEEDED after grading what it has.
- Never edit `account_runs`, `wallet_ledger` or tracker tables by hand: the lifecycle module is the
  only writer (W1) and the database refuses an invalid COMPLETED.
