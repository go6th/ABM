# Runbook: A monitor source is failing (ticket)

Owner: TODO (signals owner, docs/oncall/rotation.md)

## Symptom
`monitor_outage`: more than 20 blocked monitor polls an hour (`monitor_polls_total{outcome=blocked}`,
proposal), typically one monitor kind for many accounts (rate limits or an outage).

## Impact
Affected tracker rows are Blocked with a reason (`source_unavailable`, `source_rate_limited`), never
"no change"; items are not counted absent, so nothing is removed by mistake.

## First checks
1. The reason label and which monitor kind (dashboard).
2. The provider's status page and quota ([VERIFY] each provider's limits).
3. Whether the monitoring budget throttle reduced polls (a different alert).

## Mitigation
Wait out a provider outage; for rate limits, lengthen that kind's cadence through the shelf-life
policy (a versioned change with a reason). Never retry by hand outside the node RetryPolicy.

## Escalation
Ticket per docs/oncall/severity.md; the provider relationship owner for persistent limits.

## Verification
Blocked polls fall back to baseline; the next probes record `ok`.

## Follow-up
Record the provider behaviour in docs/open-items.md if it differs from the documented limits.
