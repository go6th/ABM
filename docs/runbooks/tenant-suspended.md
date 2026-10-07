# Runbook: A tenant was suspended (ticket)

Owner: TODO (support owner, docs/oncall/rotation.md)

## Symptom
`tenant_suspended` fired: a tenant was suspended by an operator (operator plane) or by the system.

## Impact
Within the membership-cache bound (about 60 seconds) every session and token of the tenant is
refused, new runs are refused and running runs stop at their next check. Data is kept; nothing is
deleted.

## First checks
1. Run `queries/operator_actions.sql`: who suspended which tenant, with which reason code.
2. Run `queries/tenant_overview.sql`: `suspended` is true for that tenant and `live_runs` falls to 0.
3. Confirm the reason with the requester (billing, abuse report, customer request).

## Mitigation
If the suspension was a mistake: `POST /operator/v1/tenants/{id}/reactivate` with a reason code
(operator role). Both actions are in the operator audit and in the tenant's own audit log
(customer-visible).

## Escalation
Ticket per docs/oncall/severity.md; the account owner informs the customer.

## Verification
The tenant's members sign in again after reactivation; `tenant_events` shows `reactivated`.

## Follow-up
None for an intended suspension. For a mistaken one, a short review of who could suspend and why.
