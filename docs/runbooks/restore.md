# Runbook: Restore the database (operations)

Owner: TODO (platform owner, docs/oncall/rotation.md)

## Symptom
Data loss or corruption that point-in-time recovery must repair.

## Impact
The service is degraded or down for the restore window (RTO target 30 minutes, RPO under 60
seconds: targets, proven only by a game-day on the real Cloud SQL instance).

## First checks
1. Pick the recovery time (just before the incident) from the audit and logs.
2. Confirm backups and point-in-time recovery are enabled ([VERIFY] V-P4-SQL-1).

## Mitigation
Follow `docs/phase4/dr_plan.md` steps 1 to 7. The hermetic rehearsal `ops/dr/restore_drill.py`
proves the invariants a restored database must pass: wallet balances against the ledger, one
settlement per run, facts with evidence, alerts with deltas, the RLS catalog and the isolation
suite. The audit hash-chain verification is a step of the real restore (`dr_plan.md` step 4); the
rehearsal does not run it.

## Escalation
Page the platform owner and the security owner.

## Verification
The invariant script passes on the restored instance before traffic is switched.

## Follow-up
A post-incident review with the measured RPO and RTO.
