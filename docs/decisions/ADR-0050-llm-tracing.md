# ADR-0050: LLM tracing without prompt text (D-60)

- Status: Proposed (D-60)
- Date: 2026-10-06
- Deciders: TODO

## Context
Observability work package (docs/observability/README.md). Observability must not leak tenant
data, change run behaviour, or become a second source of truth for money or run status.

## Decision
Model calls carry task kind, tier, alias, provider code, token counts, cost, degraded and fallback
flags on the `model.call` span and in `llm_calls`; never prompt or completion text. A self-hosted
LLM tracer is not adopted (owner decision D-60).

## Consequences
Prompt-level debugging uses the prompt hash and the replay eval, not stored text.
