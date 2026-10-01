# ADR-008 — Canary rollout + graph version pinning (no blue-green)

Date: 2026-10-01
Status: Accepted

## Context
Runs last hours and hold state in the old version's format; a blue-green swap abandons or
corrupts in-flight runs (v1 defect D10).

## Decision
Argo Rollouts canary (5/25/100%) with automated analysis; every run is pinned to the
`graph_version` recorded at creation, and old workers coexist until no active run references
them. Checkpoint state carries `schema_version` with expand/contract migrations.

## Consequences
Rollback is a routing change plus a version flag, not a data migration. Two worker versions
may run simultaneously during a rollout window.

## Revisit if
Canary analysis proves noisier than the stable version on run-failure ratio — then gate on
manual approval in addition.
