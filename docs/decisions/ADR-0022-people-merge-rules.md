# ADR-0022: People merge rules

- Status: Proposed
- Date: 2026-10-03
- Deciders: tech lead (TODO)

## Context

PRD s4.2 step 7: merge by profile URL first, name second. People rows are never deleted
(`references/05`). Two people can share a name; one person can appear under several URL forms.

## Decision

- Canonical profile URL: the platform URL rule (`account_intelligence.urls`), then for the
  configured profile host: https, the canonical host for every locale subdomain, no query, case
  folded path, no trailing slash.
- Union-find over the whole batch, decided pairwise so the plan is order independent: equal
  canonical URL merges; a record without a URL joins a URL group only when the normalised names
  are equal AND one corroborating signal agrees (employer domain, a shared lane, title token
  Jaccard >= 0.5) AND exactly one URL group qualifies. Otherwise `possible_duplicate`, not merged.
  Two different canonical URLs are never merged.
- The canonical member is the smallest key with a URL; every other member points at it, so there
  is no chain and no cycle. The plan is recomputed (forward and reversed) and must match.
- Storage: each found record is a `people` row; merged rows point at the canonical row's id. A
  trigger refuses a pointer at a merged row, at a row of another run or tenant, or a merge of a
  row that others point at. Merges and unmerges are `person_events`; `unmerge` is the reversal.

## Consequences

Name merges are conservative: a duplicate without corroboration stays visible as
`possible_duplicate` for review. No honorific or nickname list is applied (none is sourced).
Concurrent writers to one run are excluded by the run lease, not by the trigger.
