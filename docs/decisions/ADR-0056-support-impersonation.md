# ADR-0056: Support view-as by tenant grant only (D-66)

- Status: Proposed
- Date: 2026-10-07
- Deciders: TODO (product owner)

## Context

SaaS 3.1 rule 9 and A8: just-in-time support access with a reason code and a customer-visible audit
entry. Operators must never hold standing access to tenant data.

## Decision

* A tenant admin (fresh step-up) grants one named staff member a time-boxed grant with a reason
  code (`config/support.yaml`: read at most 120 minutes, write at most 30, proposals). The grant
  is resolved on every request by the definer `support_grant_tenant` (this operator, unrevoked,
  unexpired); revocation and expiry act on the next request.
* `read` allows safe methods only (view-as routes carry `X-Support-View: read-only` for the banner);
  any write needs a separate `write` grant; no export, spend, cap, key or user routes exist on the
  view-as plane. Every call, allowed or refused, is a `support_session_events` row and a tenant
  audit row the tenant lists under Support access. No break-glass.
* Staff tokens come from a separate staff IdP (`STAFF_JWT_*`); unconfigured means 503.

## Consequences

Proven by `tests/phase3/test_ws8_admin.py` and the staff probes of the isolation matrix. The
staff IdP and its claim names are [VERIFY].
