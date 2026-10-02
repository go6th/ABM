# ADR-0014: consolidating grading logic in `account_intelligence.grading`

- Status: Accepted (engineering)
- Date: 2026-10-02
- Deciders: engineering

## Context
Quote, URL and figure checks lived in `workflow/evidence_gate.py`; the Lead check in
`tracker/leads.py`; the engine in `agents/grading.py`.

## Decision
* `account_intelligence.grading` holds every rule. `agents/grading.py` stays the public import path
  as a facade; its legacy functions delegate to the new engine (their tests are unchanged).
* The evidence gate delegates to `grading.spans`. Quotes are now verbatim (case preserved) per G4,
  where the gate previously case-folded. Its caller-supplied `read_in_primary_this_run` is a
  pre-check only; `grade_claims` recomputes the flag in code (G5).
* `tracker/leads.py` is a thin adapter over `grading.leads` (ADR-0013).
* `record_graded_fact` is the only fact write path; the database mirrors the key rules (migration 0009).

## Consequences
The data-layer plan intends to rename the starter `facts` to `structured_facts` and create a new
`facts`; when that happens the write path and migration 0009's constraints move with it.
