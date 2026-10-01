# ADR-011 — Database host: managed Postgres 16 + pgvector (minimum viable), Aurora later

Date: 2026-10-01
Status: Accepted

## Context
The service design proposes Vercel/Supabase/Neon-class hosting; the spec proposes EKS +
Aurora + multi-region. For Phase 0/1 the team is 1–6 engineers and DR starts at V1 (spec
11.3). The conflict is recorded in T4 (C4) rather than silently resolved.

## Decision
Phase 0/1: single managed Postgres 16 with pgvector (Docker locally, managed instance in
staging/prod), forced RLS, per-tenant KMS-equivalent envelope encryption deferred to Phase B
vault work. Migrator role performs DDL only. Aurora Global Database is adopted at V1 when
the warm standby region (T88) lands; RPO/RTO targets stay as spec'd.

## Consequences
Multi-AZ + tested backups carry MVP; a migration to Aurora is a managed-database port, not a
redesign, because the schema and RLS model are portable.

## Revisit if
A pilot tenant requires a silo tier or residency outside the primary region on day one.
