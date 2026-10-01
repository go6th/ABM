# ADR-006 — Visibility labels + DB roles for the two-output rule

Date: 2026-10-01
Status: Accepted

## Context
Private client material must never reach a client-facing deliverable, even if a prompt or
renderer has a bug (attack A9). Prompt-level separation is not a guarantee.

## Decision
Every fact/chunk row carries a `visibility` label (`public` | `private`). The public
renderer connects via the `public_reader` DB role which has no grant on private-data tables
and a policy that filters `visibility = 'public'`. A separate, independent leak detector
(n-gram + embedding similarity vs private rows) runs before any public artefact is released.

## Consequences
Structural guarantee at the database layer; renderers must manage role switching inside a
transaction. The leak detector adds a cheap second line of defence.

## Revisit if
Rendering latency through role switching exceeds p99 targets — then pre-materialise the
public brief at verify time instead.
