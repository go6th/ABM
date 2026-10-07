# ADR-0062: Probe, then escalate (D-72)

- Status: Proposed
- Date: 2026-10-07
- Deciders: TODO (product owner)

## Context

ARCH 6.4 gives the method (probe the cheapest high-signal sources, escalate on delta score or
brief age) and illustrative economics, but no formula, threshold or maximum age.

## Decision

* A probe polls only the probe sources (job postings, news) through the monitor ports, with the
  monitoring allowance reserved per kind; a failed source makes its rows Blocked with a reason.
* The delta score is a documented integer weighted sum over typed delta items
  (`config/refresh.yaml` `delta_weights`, proposals).
* `refresh.escalate.decide` is pure: escalate on score >= 10 or brief age >= 90 days, unless the
  account escalated within 7 days or twice this month (proposals). The decision and its inputs are
  stored with the run.
* An escalated refresh inherits the probe's rows (`planner.plan(inherited=...)`): nothing is pulled
  twice. A full refresh re-runs High rows (PRD 4.5) and rows past their shelf life (C27).

## Consequences

Proven by `test_ws2_refresh.py` and the 26-week simulation. The refresh-to-full-run ratio is
reported from measured rows only (`governance.account_cost`).
