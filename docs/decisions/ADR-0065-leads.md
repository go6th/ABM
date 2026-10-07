# ADR-0065: A match is a Lead until read (D-75)

- Status: Proposed
- Date: 2026-10-07
- Deciders: TODO (product owner)

## Context

The roadmap names keyword matches as the main false-positive risk: "a keyword match is a lead,
graded Lead until read".

## Decision

* A monitor only proposes a candidate; the grading engine grades it.
* An immediate rule needs the item read and verified in this refresh at the rule's grade floor; a
  Lead, or anything not read in this refresh, is digest-only, labelled "Lead, not yet verified".
* An unverifiable candidate is dropped with a counted reason (`intake_drops`).

## Consequences

Proven by `test_ws3_ws4_signals.py` and `test_ws5_alerting.py`; the database refuses a Lead in
the immediate tier (`ck_alerts_lead`).
