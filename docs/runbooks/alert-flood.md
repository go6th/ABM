# Runbook: An alert flood (ticket)

Owner: TODO (alerts owner, docs/oncall/rotation.md)

## Symptom
`alert_flood`: more than 200 alert deliveries an hour (proposal).

## Impact
Alert fatigue. Per-user and per-account caps and digest batching should hold; a flood means a
rule, a fingerprint or a monitor misbehaves.

## First checks
1. Which rule and channel (dashboard); suppression reasons growing or not.
2. Whether one account dominates (a misbehaving source producing near-duplicates the clustering
   does not catch).

## Mitigation
Lower the per-user caps or raise the digest threshold (configuration change with review); a user
can mute an account. Never delete alerts or events.

## Escalation
Ticket; the alerts owner.

## Verification
Deliveries back to baseline; suppressions recorded with reasons.

## Follow-up
Add the flood pattern to the dedupe and clustering tests.
