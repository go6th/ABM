# ADR-016 — Agent runtime: LangGraph state graphs over the Claude Agent SDK

Date: 2026-10-01
Status: Accepted

## Context
The service design proposes the Claude Agent SDK with mounted skill files; the spec proposes
LangGraph with Postgres checkpoints (conflict C2 in the T4 register). The manual skill is
proven but single-operator; the platform needs durable, parallel, multi-tenant execution.

## Decision
Implement agents as LangGraph state graphs: typed `RunState`, parallel fan-out via `Send`,
`RetryPolicy` on fetch nodes, in-graph budget guard, interrupts for human approval, and
Postgres checkpoints for durability (ADR-002). The proven manual process (coverage rows,
grading, gate rules) is encoded as deterministic nodes and prompts, not as free-form agent
behaviour. Model calls route through the tiered router (ADR-010) and the tool ledger (I4).

## Consequences
Reproducible, versioned, resumable runs; the operator's tacit knowledge becomes explicit
code/tests (parity replay, T78). Anthropic models remain the default tiers via config.

## Revisit if
Spike 2 (T11) fails durability criteria, or graph fan-out cannot express a needed manual
pattern without unsafe free-form tool loops.
