# ADR-0040: the exhaustion ladder and the alternate default (D-47)

- Status: Proposed (D-47 supersedes the D-31 default; conflict C16 stays recorded)
- Date: 2026-10-06
- Deciders: TODO

## Context

PRD 3.5: supply a pre-pulled file rather than fall back to a weaker source (D-31 default: no
fallback). The roadmap item: a broker out of credits switches to an alternate path or accepts a
pre-pulled file through an upload step.

## Decision

`spend.exhaustion.decide` (pure, used by the router's exhaustion path): (1) a registered pre-pulled
file is consumed first (the connector does it before any paid call, at zero cost); (2) otherwise the
first configured alternate the tenant policy allows (`allow_alternate_on_exhaustion`, legacy key
`allow_fallback_on_exhaustion` still honoured; default `true` from `spend.yaml` v2), with a
meaning-preserving argument translation and a closed circuit, labelled with its real route; (3)
otherwise Blocked `credits_exhausted` (the failed call is the proof) and an upload request whose
exact file spec is generated in code. Exhaustion never touches a cap version (M6).

## Consequences

* The v1 router test that asserted the D-31 default now sets the tenant's refusal explicitly
  (its assertions are unchanged); a new test covers the D-47 default.
* Only one translation exists today (scrape to read_page); other alternates need planner-issued
  arguments and stay Blocked with that reason.
