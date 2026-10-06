# ADR-0041: an operator upload is hostile input (M8, M9)

- Status: Proposed
- Date: 2026-10-06
- Deciders: TODO

## Context

A pre-pulled file replaces a paid call; it must be as trustworthy as a live response and must not
open a path for malware, another account's data or a fabricated result.

## Decision

Operator only (the operator plane, D-33, is not built: the routes fail closed with 503). A signed,
size-limited upload goes to a quarantine location separate from the accepted one. One validation
pass, each step an insert-only intent event: existence, size and type caps; the malware scan
(an unavailable scanner fails closed); sha256 dedupe per run; a strict parse by the live provider's
own normaliser; account match where records carry a domain, otherwise the operator's attestation;
the attested pull date against the configured maximum age; a match to the spec's pending operation
with none registered yet. Accepted files are copied to the accepted location and registered, so the
connector consumes them at zero cost and the pull-once replay serves them. File content is never
logged, stored in an event or shown to a client; D-25 (raw payloads out of the workspace zip) and
D-10 (terms) apply.

## Consequences

* Production adapters for the store, the scanner and the parser do not exist: placeholders refuse.
* `[VERIFY]` V-UPL-1 (signed upload URL with a size condition on Cloud Storage) and V-UPL-2 (the
  malware scanning option) before production.
