# ADR-0002: Tenant isolation and RLS from the first migration

- Status: Accepted
- Source: platform skill `references/00-sources-and-conflicts.md`, conflict C3

## Decision

Tenant isolation is present from the first schema migration. Tenant-owned rows carry a non-null tenant key; PostgreSQL enables and forces RLS with `USING` and `WITH CHECK`. Runtime `app_rw` is a non-owner without `BYPASSRLS`; tenant context is transaction-local and derived from verified identity. Public rendering has its own role-scoped policy and cannot read private facts.

## Consequences

The catalog query and isolation suite are hard gates. RLS cannot be treated as a later hardening task. Checkpoint tables use tenant-prefixed thread IDs and their own policy.
