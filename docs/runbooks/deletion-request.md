# Runbook: A tenant deletion request (ticket)

Owner: TODO (support owner with a second operator, docs/oncall/rotation.md)

## Symptom
`tenant_deletion_requested` fired: a deletion request was opened (by the tenant admin with a fresh
multi-factor sign-in, or by an operator).

## Impact
Nothing is removed yet. Deletion takes two different people: a dry run, an approval by someone
other than the requester, then bounded, resumable passes. Rows on the never-delete list (ledgers,
audit chain, usage events; `config/deletion.yaml`) are kept; blocked classes (sources, people,
raw objects; conflict C23) are reported, not removed.

## First checks
1. Run `queries/operator_actions.sql` and `queries/tenant_overview.sql`: who asked, which tenant,
   the current counts.
2. Confirm the request with the customer's admin through a channel other than the app.

## Mitigation
1. Dry run: `POST /operator/v1/tenants/{id}/deletion/{request_id}/dry-run` (the counts per table
   and what is blocked).
2. A second operator approves: `.../approve` (the requester is refused).
3. Execute in passes: `.../execute` until `exit_reason` is `done`; a pass is bounded and safe to
   repeat.
Never delete by hand in SQL, never bypass RLS, never remove an insert-only ledger or audit row.

## Escalation
Ticket per docs/oncall/severity.md; counsel for legal holds (a hold blocks approval).

## Verification
`tenant_events` shows `deletion_completed`; the dry run on the same tenant reports zero for every
erasable table.

## Follow-up
Send the customer the completion confirmation; record blocked classes for C23.
