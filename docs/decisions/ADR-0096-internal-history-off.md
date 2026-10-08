# ADR-0096: Pass 1 internal history is off by default (D-98)

- Status: Proposed
- Date: 2026-10-08
- Deciders: TODO (product owner)

## Context

Pass 1 searched the operator's mail, call notes and named Drive folders (S20); in a multi-client service that is an access and privacy decision, not something to inherit (S15).

## Decision

Off for every tenant. It runs only when the tenant enabled it with admin consent, named folders and a connected Phase 3 connector (narrow scopes, access logged); content is stored private and renders only in the Internal Only view. A config default of on is refused at load and by the guard.

## Consequences

The owner decides whether to offer it at all; the connector search binding is a port with a fake today.
