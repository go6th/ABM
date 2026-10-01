# ADR-013 — Portal hosting: Next.js on managed platform, API on the same VPC boundary

Date: 2026-10-01
Status: Accepted

## Context
The service design suggests Vercel-class hosting; the spec puts everything in EKS. Team size
for Phase 1 is small and the portal is mostly a thin client over the API.

## Decision
Host the Next.js portal on a managed platform (Vercel or equivalent) with zero tenant data
persisted client-side; all data flows through the Core API. Server components call the API
with the caller's JWT only. The operator console ships in the same app behind RBAC.

## Consequences
Zero server surface to operate for the portal; SSRF-safe because only the API base URL is
reachable server-side. Phase 2 self-serve must not push tenant data into client caches.

## Revisit if
Data-residency commitments force the portal origin into the same region — then move it into
the cluster behind the same ALB.
