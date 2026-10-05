# ADR-0034: spend visibility (D-30)

- Status: Proposed (D-30 open; conflict C15)
- Date: 2026-10-04
- Deciders: TODO (product owner)

## Context

The PRD and SaaS put credits used in the delivery note; skill rule 6 forbids a credit count or a
broker name in client output.

## Decision (proposal)

- The spend report is **internal**: `spend_reports.operator_json` (provider codes, amounts,
  per-provider lines) is readable only by `app_rw` under the tenant's RLS and never written to a
  client file; the delivery note carries no spend (tested).
- The tenant gets an allow-listed projection (`tenant_json`): categories and call counts, with
  `amounts_shown: false` until D-04 gives a credit price. `public_reader` has a column grant that
  excludes `operator_json` and no grant on the other spend tables.
- The tenant view passes `scripts/scan_client_output.py`. Money figures are never logged at info.

## Consequences

Evidence: `tests/system/test_credit_cap_run.py`, `tests/isolation/test_spend_isolation.py`.
