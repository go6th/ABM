# Credit cap: spend report fields (internal)

Built once per terminal state in the finishing transaction (`spend.service.close_in_session`),
versioned, never overwritten; a re-delivery with the same ledger stores no second version. JSON
(`spend_reports.operator_json`) plus a Markdown rendering. **Never a client file** (D-30).

| Field | Meaning | Base |
|---|---|---|
| `run_id`, `account_domain`, `final_status`, `attended` | header | the run |
| `cap_versions[]` | version, cap, approved by, at | `run_cap_versions` and the approval events |
| `totals.spent_usd` | the run's counter | `account_runs.spent_usd` |
| `totals.ledger_usd` | derived spend | the ledger events |
| `totals.cap_usd`, `remaining_usd`, `percent_of_cap`, `overshoot_usd` | cap use | the approved cap |
| `by_provider[]`, `by_category[]`, `by_operation[]` | paid calls, pre-pulled calls, zero-charge calls, replays (not measured), failed after charge, open reserves, credits, USD, share, advisory, variance, flag, unknown-cost calls, possible duplicates, amount at risk | the ledger; advisory from the approved breakdown |
| `blocked_rows` | rows Blocked for `budget_cap` (the cap), `credits_exhausted`, `no_cap` | the tracker |
| `extensions` | extension requests | cap events |
| `llm_usd_informational` | model spend, not counted against the cap | supplied or `not measured` |
| `balances.start`, `balances.end` | provider balance status at start and end | balance snapshots |
| `estimated_avoided_usd` | labelled `estimate`, or `not measured` | not computed yet |
| `reconciliation` | `consistent`, or every problem found | `spend.reconcile` |

`status` is `reconciled` or `reconciliation_failed`. A missing figure reads `not measured`, never
0. The tenant view (`tenant_json`) is the allow-listed projection: run id, status, categories with
call counts, total calls, `amounts_shown: false`.
