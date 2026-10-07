# ADR-0068: Open Door is a contract, not a feature (D-78)

- Status: Proposed
- Date: 2026-10-07
- Deciders: TODO (product owner)

## Context

A later execution phase will consume signals; nothing of it may be built now.

## Decision

* `signal_event.v1` and `signal_page.v1` JSON Schemas (with examples) in `src/opendoor/schemas`;
  every emitted document is validated; additive changes only inside v1 (`breaking_changes`).
* `GET /v1/signals` with keyset cursors, tenant-scoped, role-gated, audited, client-safe. Webhooks
  use the alert signing scheme.
* No consumer, scheduler, sequencing or sending code (an AST test).

## Consequences

Proven by `test_ws8_opendoor_api.py`. Docs: `docs/opendoor/`.
