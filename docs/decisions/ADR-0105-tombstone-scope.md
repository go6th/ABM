# ADR-0105: Separate tenant and platform tombstones

**Status:** Accepted  
**Date:** 2026-10-08  
**Related:** ADR-0083, D-83, skill rule 1, migration 0043

## Context

Migration 0036 put tenant-scoped erasure records and platform-wide retention records in one table.
Its nullable `tenant_id` made the table incompatible with the required tenant-table invariant,
and it had no row-level security. Five later tenant tables also enabled RLS without forcing it.

## Decision

Tenant tombstones remain in `tombstones` with a required `tenant_id`, forced row-level security,
and an `app_rw` policy tied to `app_tenant()`. Platform retention tombstones move to
`platform_tombstones`, which has no tenant field and is insertable by `app_rw` only through the
narrow `write_platform_tombstone` function. The function cannot read tenant rows or set a tenant
scope. Existing null-scoped rows are copied with their identifiers and hashes before removal from
the mixed table.

The tenant tool-call, review, unit-cost, anomaly, and link-check tables receive `FORCE ROW LEVEL
SECURITY` and their existing policies are recreated with the same predicates.

## Consequences

Tenant tombstones are written only in `tenant_txn()`. Platform tombstones are never returned to
tenant callers. A downgrade refuses to run while platform tombstones exist, preserving their
append-only history instead of silently discarding it.
