# ADR-0060: Platform-held broker keys only (D-70)

- Status: Proposed
- Date: 2026-10-07
- Deciders: TODO (product owner)

## Context

Tenants may one day bring their own broker keys; phase one has no decision on it.

## Decision

* Broker keys are platform-held. The console stores a Secret Manager **reference** as a new
  insert-only version (`broker_keys`), write-only: no route returns the reference.
* A run uses the version in force when it was created (`admin.keys.effective_ref`).
* Bring-your-own is behind a flag and raises `NotConfigured` until decided.

## Consequences

Proven by `tests/phase3/test_ws8_admin.py`.
