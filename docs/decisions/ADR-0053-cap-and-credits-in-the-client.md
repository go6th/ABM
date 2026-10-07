# ADR-0053: Depth tiers instead of caps and credits in the client (D-63, C24)

- Status: Proposed
- Date: 2026-10-07
- Deciders: TODO (product owner)

## Context

The roadmap's start-run form takes a cap and the SaaS client approves a budget, but the roadmap and
skill rule 6 forbid credit counts, cost and provider names in client views and files.

## Decision

* The start-run form offers depth tiers (`standard`, `deep`); the tier maps internally to the
  credit-cap work (ADR-0031). The client sees plan allowance used as units and a percentage
  (`GET /v1/usage`), never credits, dollars or provider names.
* Limit refusals are 402 problems in units ("10 accounts"); the client output scan
  (`scripts/scan_client_output.py`) runs on every tenant-visible output in tests.

## Consequences

Needs the owner's decision (C24). D-04 still governs anything else a tenant may see. Proven by
WS5 and WS7 tests (no credit, cost or provider words in run, usage and limit responses).
