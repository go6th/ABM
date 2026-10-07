# ADR-0058: Seller usefulness events and metric (D-68)

- Status: Proposed
- Date: 2026-10-07
- Deciders: TODO (product owner)

## Context

The roadmap's exit criterion names a seller usefulness metric (first-call questions used,
Confirmed facts referenced) without definitions.

## Decision

* Events, identifiers only: `first_call_question_used` (marked or copied) and
  `confirmed_fact_referenced` (opened, copied into a note, or in a downloaded export that holds
  facts). The subject must be a real first-call question section or a current, public Confirmed
  fact of the tenant; one row per kind, subject, user, channel and day.
* Metric (proposal, `config/pilot.yaml`): distinct used / distinct delivered in a 30-day window,
  always with its base and a `small_base` label under 20; operators see per-tenant counts only.
* Feedback: verdict and a reason from a fixed list; the note is tenant data, never logged.

## Consequences

Partial: the client app does not yet show first-call questions on screen (needs a sixth
materialised table kind, an altered CHECK constraint), so question use is API-only. Proven by
`tests/phase3/test_ws9_pilot.py`.
