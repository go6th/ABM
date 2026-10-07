# Metering and plan limits (D-67, ADR-0057)

Values: `config/plans.yaml` (proposal; `pilot` is the default plan).

| Dimension | Counted when | pilot | standard |
|---|---|---|---|
| accounts | an account is added (all time) | 10 | 50 |
| runs_per_period | a run starts (per UTC month) | 20 | 100 |
| refreshes_per_period | a refresh starts (not built) | 0 | 200 |
| seats | a member becomes active | 10 | 25 |
| connectors | a connection is started | 3 | 5 |
| storage_mb | not enforced yet | 1024 | 10240 |

* Usage events (`account_added`, `run_started`, `run_settled`, `refresh_run`, `export_created`) are
  insert-only with a unique `kind:subject` key, written in the action's transaction.
* A limit check is one conditional update on `usage_counters` (derived): 100 concurrent requests
  for the last unit never pass the limit (test). Releases (member removed, connector disconnected)
  decrement the same counter.
* Refusal: 402 `plan_limit_reached`, in units ("10 accounts"), never cost or credits.
* Soft threshold 80%: one `plan_usage_near_limit` notice per period and dimension.
* Quota overrides (`quota_versions`) and plan assignments are insert-only versions by operators,
  audited on both planes; the newest wins.
* `GET /v1/usage` and `/v1/usage/export` (admins) show units and percentages.
* `metering.reconcile` compares the stream with runs, the wallet ledger, accounts and members and
  reports drift (it never repairs silently).
