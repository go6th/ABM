# ADR-0059: Resource-level read audit with a hash chain (D-69)

- Status: Proposed
- Date: 2026-10-07
- Deciders: TODO (product owner)

## Context

SaaS 3.1 rule 7 asks which broker was called; rule 6 forbids provider names in client output.
ARCH 14 sets 90 days queryable and 7 years archived.

## Decision

* Every read, run, export and admin route has an audit class in the route catalog; reads are
  recorded by the router dependency at resource level, deduplicated per actor, action and
  resource for 60 s (proposal), identifiers only; admin and export actions fail closed (503) when
  the audit row cannot be written.
* `audit_events` is insert-only with a per-tenant hash chain under an advisory lock; verification
  runs in bounded batches and detects a deleted or edited row.
* The tenant export shows provider calls by **category** only and passes the client output scan.

## Consequences

Retention archive with Object Lock is not built (Blocked). Proven by
`tests/phase3/test_ws3_audit.py`.
