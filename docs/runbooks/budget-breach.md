# Runbook: A tenant reached its monitoring allowance (ticket)

Owner: TODO (governance owner, docs/oncall/rotation.md)

## Symptom
`monitoring_budget_breach`: a reservation was refused at 100% of a tenant's monthly monitoring
allowance.

## Impact
Monitoring of that tenant's accounts stops until the month resets (no rollover); the tenant saw the
80% notice earlier and sees the meter at 100%. Probes of priority A accounts were reduced last.

## First checks
1. The tenant's throttle events (alert_80, ladder steps, hard_stop) and cost per monitored account
   (operator plane).
2. Whether a misbehaving monitor inflated usage (compare monitor runs per account).

## Mitigation
If the plan is right, nothing: the allowance works. If the tenant needs more, an operator sets a new
allowance version with a reason code. Never reset usage counters by hand.

## Escalation
Ticket; the account owner talks to the customer.

## Verification
Reservations succeed after a new allowance or the month boundary.

## Follow-up
Review the plan allowance against measured cost per account.
