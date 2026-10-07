# Runbook: A CRM write failed (ticket)

Owner: TODO (CRM owner, docs/oncall/rotation.md)

## Symptom
`crm_write_failed`: a write ended `failed` (refused by the CRM, or not found by its marker after a
crash).

## Impact
A note, task or snapshot did not reach the customer's CRM. Nothing was overwritten or deleted: the
adapter can only create.

## First checks
1. The write's events in the tenant's CRM writes list (requested, approved, sending, outcome).
2. The tenant's CRM connection state and the CRM's status ([VERIFY] V-P4-CRM-1).

## Mitigation
The tenant admin re-requests the write after fixing the cause (a new write is idempotent by its
content key). Never write to the CRM by hand, never update an existing record.

## Escalation
Ticket; the CRM owner.

## Verification
The new write reaches `created`.

## Follow-up
If the CRM changed its API, update the adapter through review.
