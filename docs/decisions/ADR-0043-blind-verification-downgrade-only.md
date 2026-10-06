# ADR-0043: blind source re-read, downgrade only (D-49)

- Status: Proposed (D-49)
- Date: 2026-10-06
- Deciders: TODO

## Context
Roadmap: a fresh-context verifier re-reads the source for every Confirmed claim, checks figures and
rereads the narrative; disagreements downgrade and are logged. Skill: only the grading engine
computes a grade; a downgrade is a new superseding row.

## Decision
Inside `distill_grade`, after the support judge and the fact writes, every Confirmed fact's stored
source (the run's own `RunSource`) is re-read by a tool-less call that is never shown the value.
Code checks the quote is in the source, the answer is in its quote, and the normalised answer
equals the claim. Any other outcome (value mismatch, not found, unreadable, unavailable, same
family) supersedes the fact with the grading engine's G12 cap through `grading.repo.supersede`
(actor `verifier`, reason `verifier_<outcome>`) and is a `verifier_findings` row. Figures are
compared with the number registry in code; narrative verdicts act by template (keep, rephrase,
remove). No verifier output can add, edit or raise anything.

## Consequences
* With one model family (C20) every Confirmed claim is capped to Probable until a second family
  is configured: recall cost, never a false Confirmed.
* The narrative reread is built and tested but has no narrative to read yet (no stage writes a
  narrative; OI-EXP-1).
