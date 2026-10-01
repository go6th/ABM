# ADR Index

All 16 Phase 0 architecture decision records. Status of each: **Accepted** (T8 exit criterion).

| ADR | Decision | File |
|-----|----------|------|
| 001 | Pooled RLS tenancy with tier upgrade path | [ADR-001](ADR-001-pooled-rls-tenancy.md) |
| 002 | LangGraph checkpoint as durability source of truth | [ADR-002](ADR-002-langgraph-checkpoint.md) |
| 003 | Inngest as outer trigger, schedule and slice loop | [ADR-003](ADR-003-inngest-outer-loop.md) |
| 004 | pgvector halfvec(1536) with hash partitions | [ADR-004](ADR-004-pgvector-halfvec.md) |
| 005 | Quarantine extractor for untrusted text | [ADR-005](ADR-005-quarantine-extractor.md) |
| 006 | Visibility labels + DB roles (two-output rule) | [ADR-006](ADR-006-visibility-labels-db-roles.md) |
| 007 | Reserve / commit / refund wallet ledger | [ADR-007](ADR-007-reserve-commit-refund-wallet.md) |
| 008 | Canary rollout + graph version pinning | [ADR-008](ADR-008-canary-version-pinning.md) |
| 009 | Self-hosted Langfuse + OpenTelemetry | [ADR-009](ADR-009-self-hosted-langfuse-otel.md) |
| 010 | Tiered model routing by configuration | [ADR-010](ADR-010-tiered-model-routing.md) |
| 011 | Database host: managed Postgres now, Aurora at V1 | [ADR-011](ADR-011-database-host.md) |
| 012 | Auth provider: external IdP, JWT tenant/role claims | [ADR-012](ADR-012-auth-provider.md) |
| 013 | Portal hosting: managed Next.js, data via Core API only | [ADR-013](ADR-013-portal-hosting.md) |
| 014 | Vector store: pgvector in the primary database | [ADR-014](ADR-014-vector-store.md) |
| 015 | Object storage: SSE-KMS, prefix-scoped, Object Lock audit | [ADR-015](ADR-015-object-storage.md) |
| 016 | Agent runtime: LangGraph state graphs | [ADR-016](ADR-016-agent-runtime.md) |

Rules: an ADR is never deleted; superseded ADRs are marked `Superseded by ADR-xxx` and kept
(CI check `scripts/check_adrs.py` fails on a dropped or non-Accepted ADR number).
