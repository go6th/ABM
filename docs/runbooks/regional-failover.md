# Runbook: Regional failover (operations)

Owner: TODO (platform owner, docs/oncall/rotation.md)

## Symptom
The primary region is unavailable.

## Impact
Full outage until failover. Cross-region DR is not built or tested here (Blocked until staging).

## First checks
1. The cloud provider's status for the region.
2. The replica's lag and health ([VERIFY] V-P4-SQL-2).

## Mitigation
Follow `docs/phase4/dr_plan.md`: promote the replica, point the services at it, run the invariant
script, re-enable schedulers last (slots catch up in bounded batches).

## Escalation
Page the platform owner, the security owner and the product owner.

## Verification
Invariants pass; probes and alerts resume; the freshness SLI recovers.

## Follow-up
A game-day to measure RPO and RTO against the targets.
