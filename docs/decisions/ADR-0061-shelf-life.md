# ADR-0061: Shelf life per signal type (D-71)

- Status: Proposed
- Date: 2026-10-07
- Deciders: TODO (product owner)

## Context

The sources give cadences (SaaS 5.1, checklist `refresh_cadence`) and confidence half-lives
(hiring 21, technology 90, leadership 180, funding 365 days) but no shelf-life durations.

## Decision

* `config/shelf_life.yaml`: one entry per signal type with a cadence (weekly, monthly, quarterly)
  or null, and a `source` or `status` on every value. Shelf life equals the cadence period (7, 30,
  91 days: proposals), separate from the grading half-lives.
* `refresh.policy.is_due` is pure: due when `now >= last_checked_at + shelf life`; never checked is
  due; a null cadence is never scheduled (not "default weekly").
* Tenants may only lengthen a shelf life, within `tenant_override_bounds` (1x to 4x, proposal),
  through insert-only `shelf_life_overrides`. Policy changes affect future due dates only.

## Consequences

Proven by `tests/phase4/test_ws1_policy.py`. Values need the product owner.
