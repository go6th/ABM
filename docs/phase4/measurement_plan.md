# Measurement plan: refresh ratio, alert usefulness, 20 monitored accounts

| Target (to validate) | Instrument | How it is reported | State |
|---|---|---|---|
| A refresh costs a measured fraction of a full run | `governance.account_cost.refresh_ratio` over `tool_calls.cost_usd` | ratio with base, counts and as-of; "not enough data" below 10 runs each side | report built; needs real runs |
| Sellers rate at least 70% of alerts useful | `alert_feedback` (useful, not useful, fixed reasons) | useful / rated, with rated / delivered and a small-base label under 20; 70% shown as a target, never a result | instrumented; needs real alerts rated |
| At least 20 monitored accounts per pilot tenant | `monitor_configs` per account | count of accounts with an active monitor set | needs real pilots |
