# Runbook: a spend report stamped `reconciliation_failed`

Symptom: `spend_reports.status = 'reconciliation_failed'` or the nightly job reports a run.

1. Read the problems: `operator_json -> 'reconciliation'`. Each names its figures:
   `spent X != ledger Y` (the counter disagrees with the ledger), `parts X != ledger Y` (a rollup
   defect), `run cap (v, c) != latest approved (v, c)` (the enforcement columns were changed outside
   an approval), `rollup differs from the connector ledger sum` (the two derivations drifted).
2. Check open intents: `unreconciled_usd > 0` means attempts started and never finished (a killed
   worker). Their reservations stay counted (never under-counted). Resume the run so the ledger
   reconciles them, or confirm with the provider's invoice whether the call was billed.
3. Compare with the provider invoices: by provider code and operation (`run_spend_by_provider`),
   credits per call (`tool_call_events.credits`).
4. Never edit `spent_usd`, the ledger or a stored report: they are insert-only or written by one
   code path. A correction is a new report version after the cause is fixed and the run re-closed.
