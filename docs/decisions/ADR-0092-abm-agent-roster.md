# ADR-0092: ABM agent roster and boundaries (D-95)

- Status: Proposed
- Date: 2026-10-08
- Deciders: TODO (product owner)

## Context

The skill was run by one operator in the loop. Production needs bounded agents with rules enforced in code.

## Decision

One agent per step where a model is needed (setup, Pass 1, Pass 2 per source group, People, Sense check, Distill); build, gate and delivery are code. The Pull agent is a code-driven fetcher with no model and an output schema (`ManifestRow`) that has no interpretive field. Each agent has a tier alias and task kind (split mode, G11), a typed tool allow-list, strict input and output schemas, budgets and guards (`config/agents.yaml`; registry test).

## Consequences

Agents never grade, track, price or render: those stay with the grading engine, tracker, credit gate and exporters behind ports. Budgets are proposals until measured.
