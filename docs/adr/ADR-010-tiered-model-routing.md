# ADR-010 — Tiered model routing by configuration

Date: 2026-10-01
Status: Accepted

## Context
Hard-coded model IDs and prices rot within months (v1 defects D3, D5); cost and quality
must be tunable per node without code changes.

## Decision
Nodes address models by tier — `planner`, `extractor`, `judge`, `embedding`, `rerank` —
resolved from configuration, never hard-coded. Concrete IDs are dated/verified in the
verification register (V1). Model changes ship through the eval gate (paired comparison on
the golden set) and are recorded per run for reproducibility.

## Consequences
Provider deprecations become config changes; the judge model must differ from the generator
per evaluation hygiene.

## Revisit if
Tier resolution adds measurable latency at fan-out — then cache resolved IDs per process
with a short TTL.
