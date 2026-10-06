# ADR-0049: paging policy and alert catalog (D-59)

- Status: Proposed (D-59)
- Date: 2026-10-06
- Deciders: TODO

## Context
Observability work package (docs/prompts/build_observability.md). Observability must not leak tenant
data, change run behaviour, or become a second source of truth for money or run status.

## Decision
Alerts live in `config/telemetry/alerts.yaml`; each names a catalog metric or SLO, a severity from
`severity.yaml`, an owner and an existing runbook, and is generated into Terraform JSON
(`infra/modules/observability/generated_*.tf.json`). Pages: availability burn, isolation (canary leak, probe
leak, RLS-denied spike), telemetry silence. Everything else tickets. Every alert has a firing
scenario and a healthy timeline in `tests/obs` (our semantics; Google's evaluator is V-OBS-2).

## Consequences
The on-call tool, rotation and channels are owner inputs; channel ids are Terraform variables.
