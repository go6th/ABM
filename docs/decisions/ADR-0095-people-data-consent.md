# ADR-0095: People data consent (D-97)

- Status: Proposed
- Date: 2026-10-08
- Deciders: TODO (product owner)

## Context

The skill's people source requires a consent flow before people pulls (S19). The consent text is in the archive (tools.md line 66), which is absent.

## Decision

A tenant-level insert-only consent record (`people_data_consents`: who, when, text version, scopes; revocation is a new row) must exist, unrevoked, with scope `people_data` and the current approved text version before any people pull; otherwise the rows are Blocked `consent_required` and no call is made. The tenant sees "People data consent", never a provider name. Until the owner approves the archive's text, `current_text_version` is null and no record is valid.

## Consequences

Every people row is Blocked in production until the consent text is approved and recorded. Consent capture UI is a follow-up.
