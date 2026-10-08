# ADR-0094: Sense check and approval of recommended pulls (D-96)

- Status: Proposed
- Date: 2026-10-08
- Deciders: TODO (product owner)

## Context

Phase 2 starts with a mandatory sense check (S10) whose recommended pulls run once approved (S18); the checklist requires asking before unapproved spend (S17).

## Decision

The sense check is a graph node before any build; the build node requires a stored result over the same manifest and facts digest (G6). Contradictions become Conflicting through the grading port. Proposals are typed, priced in code and routed by `spend.gate.decide`: within the approved cap they run; otherwise an attended run pauses (`sense_approval` interrupt, credit approval request) and an unattended run records them with the rows Partial `pull_not_approved`. After targeted pulls the sense check runs once more, record only (one bounded loop).

## Consequences

Covered pulls run without a pause because the approved cap is the approval; the owner may prefer an explicit approval for every sense-check pull.
