# Runbook: Restore the database (operations)

Owner: Platform on-call (the role on duty in docs/oncall/rotation.md; the person changes with the rota)

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

## PITR game-day checklist (quarterly, on staging; record RPO and RTO)
Point-in-time recovery on the real Cloud SQL instance is the only proof of the targets above.
Run it on staging with no research in flight; nothing here touches production.

1. Before: note the instance name, `terraform output sql_connection_name`, the current write
   position (`SELECT now(), pg_current_wal_lsn()`), and that backups and PITR are on
   (`gcloud sql instances describe <instance> --format='value(settings.backupConfiguration)'`).
2. Write a marker row through the application (one synthetic alert or note in the demo tenant)
   and record its timestamp T1 to the second.
3. Pick the recovery point T2 = T1 + 60 s, then make one more marked write after T2 (it must be
   absent after the restore).
4. Start the clock (RTO). Clone to T2: `gcloud sql instances clone <instance> <instance>-gameday
   --point-in-time T2` (a clone, never an in-place restore, so staging keeps running).
5. On the clone: the marker from step 2 exists, the write from step 3 does not; the newest
   `audit_events` row is older than T2. Record RPO = T2 - (newest committed row on the clone).
6. Run the invariant pass against the clone (`ops/dr/restore_drill.py` with the clone's
   DSN) and the audit hash-chain verification (`docs/phase4/dr_plan.md` step 4).
7. Point a scratch api revision at the clone (a second Cloud Run service, not the live one) and
   check `/readyz` is 200 with the expected schema revision; sign in once.
8. Stop the clock: RTO = step 7 done - step 4 started. Both numbers go in the review below.
9. Delete the clone and the scratch revision; confirm the live instance's backups are untouched.
10. Record in the game-day log (docs/oncall): date, operator (Platform on-call), instance,
    T1/T2, measured RPO and RTO against the targets (60 s, 30 min), what slowed the restore,
    and the follow-up tickets. A miss on either target opens an incident review, not a retest.
