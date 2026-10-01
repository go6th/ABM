# ADR-015 — Object storage: S3-compatible with SSE-KMS, prefix-scoped, Object Lock for audit

Date: 2026-10-01
Status: Accepted

## Context
Raw broker payloads, uploaded documents, rendered artefacts and the audit archive need
durable, cheap storage with per-tenant encryption context and immutability for audit.

## Decision
Use S3-compatible object storage with SSE-KMS and an encryption context carrying the tenant
id. Keys are `tenant_id/account_id/...` and workers receive short-lived, prefix-scoped
credentials per job. The audit archive uses Object Lock (compliance mode) for 7 years.
Locally and in CI this is MinIO-compatible; in production, S3.

## Consequences
Deletion propagates to S3 within 24 h (Phase B promise); audit records cannot be tampered
with; presigned uploads bypass the API so large files never hurt API p99.

## Revisit if
A silo-tier customer supplies their own bucket/KMS — then parameterise the storage module
per tenant tier.
