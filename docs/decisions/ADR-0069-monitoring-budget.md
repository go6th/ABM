# ADR-0069: Monthly monitoring allowance and throttle ladder (D-79)

- Status: Proposed
- Date: 2026-10-07
- Deciders: TODO (product owner)

## Context

Roadmap: a monthly monitoring budget per tenant, automatic throttling, an alert at 80%. No
amounts or ladder are sourced.

## Decision

* Allowance in plan units per month (pilot 2000, standard 10000: proposals); tenants see percent
  only; operators set versions with a reason.
* Every reservation is one conditional update; an unpriced call reserves the upper bound (50
  units, proposal); exhaustion is a typed refusal.
* 80%: one tenant notice per month. Ladder (pure): from 80% the lowest-scoring half of
  non-priority accounts probe-only; 90% cadences doubled; 95% non-priority paused; 100% stop.
  Priority A last. Reset at the month boundary without rollover.

## Consequences

Proven by `test_ws6_governance.py` (130-way race, alert once, ladder order).
