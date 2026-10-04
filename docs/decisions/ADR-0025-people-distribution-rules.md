# ADR-0025: People distribution rules

- Status: Proposed
- Date: 2026-10-03
- Deciders: product owner (TODO)

## Context

The routing adapter rules say the organisation provider's output is never routed to a hosted
file. The skill forbids broker names, credit counts and call references in client output, and
grading rule G11 treats two providers about one person as one source unless the registry says
otherwise.

## Decision

- Providers listed in `internal_only_providers` (today the organisation provider) mark their
  records, profiles and posts `internal_only`. A person known only from such records is counted in
  the People List Summary ("kept off this file by the distribution rules") but never listed by
  name in an export.
- The People List shows only in-scope, non-erased, non-internal people; the Summary shows counts
  by status and reason, never names; no provider code, credit figure or call reference appears.
  Every cell passes `assert_client_safe`, and the written files pass `scan_client_output.py`.
- Source independence is left to the grading engine and the source registry (provider codes are
  their own lineage group, flagged `lineage_known: false`).

## Consequences

The product owner confirms whether "hosted file" covers the People List deliverable; if not, the
flag can be narrowed to the workspace zip and the shared folder.
