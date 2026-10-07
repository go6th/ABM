# Monitoring budget and the throttle ladder (CG1 to CG3, D-79)

Monthly allowance in plan units (proposals: pilot 2000, standard 10000), reserved per poll by one
conditional update; an unpriced call counts at 50 units; exhausted is a typed refusal. Tenants see
percent only. At 80% (sourced): one notice per month. Ladder (pure, `governance.throttle.plan`):

| Used | Step |
|---|---|
| >= 80% | the lowest-scoring half of non-priority accounts probe-only |
| >= 90% | cadences doubled for non-priority accounts |
| >= 95% | non-priority monitors paused |
| 100% | everything stops until the month resets (no rollover) |

Priority A accounts are reduced only at the hard stop. Cost per monitored account (monitor units
by account, kind and month) and the refresh-to-full-run ratio (measured micro-USD, "not enough
data" below 10 runs each side) are operator reports.
