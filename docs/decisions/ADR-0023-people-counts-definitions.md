# ADR-0023: People counts definitions (D-22)

- Status: Proposed
- Date: 2026-10-03
- Deciders: product owner (TODO)

## Context

PRD s4.2 step 7: alumni, tenure and certification counts are computed in code and stated with
their base. The sources do not define alumni, the tenure buckets or the certification matching.

## Decision

- Counts are built only from rows by `people.counts.compute_counts`; no function takes a count.
  Sentences are rendered from those integers and name their base.
- Alumni: in-scope people with a read profile whose prior employers include an employer on the
  run's reference list (the pursuing client plus employers the operator names, from run input).
  Without a list the count is not computed (`waiting_on_account_owner`), never 0. Employers
  compare in name normal form; configured aliases apply (none supplied).
- Tenure: from the join date, else the role start, at the run's `as_of`; precision is kept and a
  bucket is assigned only when the whole range of possible months falls in one bucket. A future
  date or a role start before the join date is `date_invalid`. Buckets (proposal): under 1 year,
  1 to 3, 3 to 5, 5 and over.
- Certifications: only the structured field; a certification counts for a lane when a lane
  lexicon term appears in it. "Field not provided" is counted apart from "none listed".

## Consequences

`docs/people/counts.md` is the generated contract. The product owner confirms the definitions;
until then they are proposals.
