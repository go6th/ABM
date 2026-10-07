# Runbook: An inbound webhook storm (ticket)

Owner: TODO (signals owner, docs/oncall/rotation.md)

## Symptom
`webhook_storm`: more than 500 inbound monitor webhooks in 5 minutes (proposal).

## Impact
Possible replay or abuse. Every delivery is verified (source, signature, timestamp tolerance) and
deduplicated by event id; refused deliveries are counted drops, never facts.

## First checks
1. The verdict label: `replay`, `bad_signature` or `stale` dominating means abuse or a misbehaving
   provider; `ok` dominating means a real burst.
2. Which source (provider code) on the dashboard.

## Mitigation
For abuse: rotate that source's signing secret (Secret Manager reference) and block the source at
the edge. For a real burst: nothing; processing is idempotent and bounded.

## Escalation
Ticket; page the security owner if signatures fail at volume.

## Verification
Receipt rate back to baseline; drops stop growing.

## Follow-up
Review edge rate limits for the inbound endpoint.
