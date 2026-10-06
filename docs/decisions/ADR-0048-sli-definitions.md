# ADR-0048: SLI definitions and burn-rate alerting (D-58)

- Status: Proposed (D-58)
- Date: 2026-10-06
- Deciders: TODO

## Context
Observability work package (docs/prompts/build_observability.md). Observability must not leak tenant
data, change run behaviour, or become a second source of truth for money or run status.

## Decision
SLOs are in `config/telemetry/slos.yaml`. Burn rate is exact (`fractions.Fraction`); no traffic
is NoData, never 100% good. A page needs the long window and its short window above the factor
(14.4x over 1 h with 5 m; 6x over 6 h with 30 m) and a minimum event count. Run completion counts
COMPLETED or PARTIAL within the p95 target with human wait excluded and CANCELED excluded. Latency
SLIs from histograms report the straddling bucket as their error bound.

## Consequences
Windows, minimum samples and several thresholds are proposals awaiting the owner (D-58).
