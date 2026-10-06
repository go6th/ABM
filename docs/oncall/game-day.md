# Game day and chaos plan (quarterly)

Each scenario names its expected alert and runbook; record whether it fired and how fast.

| Scenario | Expected alert | Runbook |
|---|---|---|
| Collector down | telemetry_down / telemetry_dropping | telemetry-down.md |
| Planted canary in a log line | isolation_canary_leak | log-canary-hit.md |
| Broken RLS policy in staging | isolation_probe_leak | isolation-violation.md |
| Prompt prefix changed (cache invalidated) | cache_ratio_low | cache-ratio.md |
| Worker killed mid-slice | stuck_running (until reconciled) | stuck-runs.md |
| Provider returns 429 storm | provider_failures | provider-outage.md |
