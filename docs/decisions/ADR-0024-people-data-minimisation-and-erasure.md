# ADR-0024: People data minimisation and erasure (D-23, pending counsel D-06)

- Status: Proposed
- Date: 2026-10-03
- Deciders: counsel and product owner (TODO). This is not legal advice.

## Context

D-06 (which people attributes are in scope) waits for counsel; `docs/data-handling-people-data.md`
is a draft. The PRD forbids deleting client records, while data protection law may require an
erasure process.

## Decision

- Allow-list: name, current title, headline, employer, prior employers, join date, role start,
  certifications, location as returned, the person's own description of their work, last
  activity date. Anything else a provider returns is dropped at the boundary and counted
  (`fields_dropped`). No email, phone, personal link, photo, age, gender or family data is stored.
  Email addresses and phone numbers inside free text are replaced with `[redacted]` before
  storage. Raw pages stay in the payload store under neutral keys.
- The run record holds no name, title or text.
- Tombstone erasure (`PgPeopleRepo.erase`, not exposed through the API): keeps the person key,
  status, canonical profile URL (as a suppression key: an erased person is never pulled again) and
  the merge chain; nulls every personal field in people, sources, profiles and posts; records an
  `erase` event. Triggers refuse refilling or un-erasing; the append-only tables allow no other
  update.

## Consequences

Counsel must confirm the allow-list, whether the canonical URL may be kept as a suppression key,
and the erasure of payload-store pages and of graded facts derived from people claims (not
covered here). No contact-detail enrichment operation exists in any port.
