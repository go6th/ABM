# Disaster recovery plan (HD3; the sourced seven steps adapted to Google Cloud)

Targets (sourced): RPO under 60 seconds, RTO 30 minutes, proven only by a game-day on the real
environment. The hermetic rehearsal (`ops/dr/restore_drill.py`, report
`restore_drill_report.json`) proves the invariant checks, not the targets.

1. **Declare** the incident; freeze deploys; page the platform and security owners.
2. **Stop writers**: scale workers to zero; pause the scheduler (slots wait; nothing is lost).
3. **Restore**: Cloud SQL point-in-time recovery to a new instance at the chosen time, or promote
   the cross-region replica for a regional failure ([VERIFY] V-P4-SQL-1, V-P4-SQL-2).
4. **Verify** on the restored instance before traffic: the RLS catalog check (no rows), wallet
   balances equal the newest ledger rows, one settlement per run, facts have evidence, alerts have
   deltas, audit hash chains verify, the isolation suite passes.
5. **Switch** the services to the restored instance (secret reference update), start the API.
6. **Resume** workers, then the scheduler: missed slots catch up in bounded, jittered batches;
   the reconciler re-sends queued runs; stale slots recover by lease.
7. **Review**: measured RPO and RTO, a blameless post-incident review
   (`docs/postmortems/TEMPLATE.md`).
