# ADR-001 — Pooled RLS tenancy with tier upgrade path

Date: 2026-10-01
Status: Accepted

## Context
The platform must isolate many tenants cheaply while offering contractual isolation to
enterprise buyers. Alternatives: schema-per-tenant and database-per-tenant.

## Decision
Pooled Postgres tenancy with `FORCE ROW LEVEL SECURITY` on every tenant-owned table,
transaction-local `app.tenant_id`, and a fail-closed accessor `app_tenant()`. Bridge and
silo tiers remain an upgrade path (dedicated vector partitions / dedicated cluster + KMS key).

## Consequences
Cheapest isolation that still holds under pooling; requires a migration lint and a hard
isolation CI gate (T10) to keep it honest. Regulated buyers wait for the silo tier.

## Revisit if
Spike 1 (T10) shows cross-tenant reads over a pooled 5-connection pool, or a launch customer
contractually requires a silo on day one.
