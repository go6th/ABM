# ADR-0042: model routing, fallback as selection (D-48)

- Status: Proposed (D-48)
- Date: 2026-10-06
- Deciders: TODO

## Context
ARCH: tiers are configuration; a fallback chain within a tier before degrading; one retry owner
(the node RetryPolicy) for LLM overload. Nothing in the sources names models, prices or limits.

## Decision
`config/model_routing.yaml` maps each task kind to a tier and each tier to an ordered candidate
ladder; it is the only place a model id appears. `llm.routing.select` is a pure function of the
config, the call's failed attempts and the shared candidate health. Fallback is selection: after
`fallback_after_retryable` overloads, after `ModelUnavailable`, or after `ContextTooLong` (to a
known larger window) the next attempt (re-executed by the node RetryPolicy) goes to the next
candidate; schema, authentication and content failures never fall back. Calls are insert-only
`llm_calls` rows keyed by call key and replayed at no cost; a non-primary candidate is recorded
`degraded`. Tenant or private data needs a candidate with verified zero retention (V5). Judge
independence is by model family. Every call site names a registered task kind (`RunDeps.model`);
without a router the legacy `LlmPort` serves it under the same tier as before.

## Consequences
* The router is opt-in (`MODEL_ROUTER=on`) and refuses to start while `benchmark_report` is null.
* Only the Claude family is configured, so judge independence fails closed (C20).
* The installed SDK has no temperature parameter (C21); configured temperatures are null.
