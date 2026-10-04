# ADR-0032: beyond the cap (D-28)

- Status: Proposed (D-28 open; conflict C14)
- Date: 2026-10-04
- Deciders: TODO (product owner)

## Context

The PRD says ask before spending beyond the agreed cap; ARCH says a run that hits its budget ends
BUDGET_EXCEEDED with partial results.

## Decision (proposal)

- **Unattended or scheduled run** (`account_runs.attended = false`, the default): BUDGET_EXCEEDED at
  once; never an extension.
- **Attended run**: at most `max_extensions` (1) extension requests per run, each a new insert-only
  cap version that is strictly higher than the current cap and not below spend, needing a fresh
  human approval bound to its hash; decline or timeout leaves the run at its cap
  (BUDGET_EXCEEDED). Never an automatic extension; no agent path can request or approve one.
- **Within the cap**, after the first approval, no approval request is ever raised (K11).
- Before stopping, the budget node funds High rows first (K10); unfunded rows end Blocked with the
  tracker's `budget_cap` reason (the spec's `cap_reached`: one code for one meaning).
- Tenant auto-approval (`policy_json.auto_approve_within_reserve`) covers only an initial cap at or
  below the run's created budget with no excluded provider.

## Consequences

Evidence: `tests/spend/test_db.py` (extensions), `test_oracles.py` (extension oracle),
`tests/system/test_credit_cap_run.py` (one approval, no interrupt), `test_capguard.py` (K10).
