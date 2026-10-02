# ADR-0010: product grade precedence and caps (D-01 extension)

- Status: Proposed (needs product owner; D-01 is open)
- Date: 2026-10-02
- Deciders: TODO (product owner)

## Context
The PRD (3.4 "Grading and writing", Appendix B) defines the product grades; Architecture 6.2 the
engine grades. Their mapping is open decision D-01 (`assets/grading.yaml` `proposed_mapping`). The
grading engine must store both and derive the product grade by one pure function.

## Decision (proposal, in `config/grading_policy.yaml` `product_precedence`)
First matching rule wins: (1) no evidence: Hypothesis; (2) Conflicting: at most Probable;
(3) internal material only: Internal; (4) read in a primary source this run (computed by
`primary.py`): Confirmed; (5) engine A or B: Probable; (6) engine C or a synthesis: Hypothesis;
(7) engine D or secondary/aggregator only: Lead. A Blocked attribute is a Hypothesis with its reason.
Caps applied after precedence and recorded in `flags`: judge unavailable or not configured (G12)
caps engine A/B at C and Confirmed/Probable at Probable (`unjudged`); D-11 and D-14 caps (ADR-0011,
ADR-0012). Grade A uses the tier of each group's strongest contributor (the reference's reading).

## Consequences
* Confirmed is decided only by the primary-read rule; the engine grade is stored beside it.
* With the current `config/models.yaml` (one profile for judge and generator) the judge is refused
  (`ensure_judge_differs`), so every A, B and Confirmed is capped until a distinct judge is set.
* Rule (7) is reached only for engine D in the literal order; an aggregator-only engine C becomes a
  Hypothesis. Decide whether "secondary only" should move above rule (6).
