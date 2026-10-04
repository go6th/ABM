# API core implementation status

This document describes the tenant-scoped API increment in this branch. It is not a claim that
the API v1 work package is complete.

## Implemented

- JWT verification pins RS256 and validates issuer, audience, expiry, issued-at time, subject,
  tenant claim, and role claim. Membership is checked against the tenant-scoped database and
  cached for at most 60 seconds.
- Registered v1 routes have a role/principal policy entry. A missing entry denies the request.
- API errors use problem-details JSON with a server-generated request id. Validation and
  unexpected errors do not echo request values or internal exception details.
- Run creation requires an 8-to-64 character `Idempotency-Key`. A tenant-scoped database claim
  serializes concurrent requests; the canonical body hash rejects changed replays, and the
  serialized original response is retained. The idempotency claim, account/run insert, wallet
  reservation, and ledger row share the existing tenant transaction, so a 402 rolls them back.
- Run status reads use the verified tenant transaction and return 404 for missing or
  out-of-tenant ids. ETags support conditional reads.
- Run domains are normalized and checked for malformed/IDN/IP/local forms, blocked-entity
  policy, and non-public DNS answers before database work. No production resolver or blocklist
  is configured in this repository; those dependencies return a typed 503 rather than a fake
  success.
- The token-bucket implementation is configurable and has deterministic tests. Product limit
  values and a shared production limiter are not configured, so the limiter is not yet wired to
  public routes.
- Credit prices have no defaults while D-04 is open; an unconfigured price returns a typed 503
  before the wallet reservation.

## Not implemented in this increment

Account create/list and signed cursor pagination, API-key lifecycle, run cancellation, tracker
progress, delivered artifacts, audit events, cap/spend operations, the operator plane, and the
console remain blocked or deferred. In particular, the current run route still owns its
transaction directly; a workflow-owned creation service with tracker seeding and an outbox is
not present. Its Inngest dispatch failure/reconciliation guarantee is therefore not proven.

The cross-tenant operator index is intentionally absent: there is no safe metadata-only index
source or authorized projection lifecycle yet, and tenant RLS is not bypassed to approximate
one. Product proposals D-32 through D-37 remain proposals until reviewed.
