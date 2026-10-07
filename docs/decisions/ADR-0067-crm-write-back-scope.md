# ADR-0067: CRM write-back creates, never overwrites (D-77, C28)

- Status: Proposed
- Date: 2026-10-07
- Deciders: TODO (product owner)

## Context

The roadmap allows "enriched account fields" and also "new records only, never overwrite";
updating a field of an existing record is an overwrite.

## Decision

* The client interface has create and read-only lookups only; `assert_create_only` refuses any
  adapter class with another public method (checked at import and in tests).
* Notes and tasks are new records; enriched fields go into NEW snapshot records, never existing
  fields. Writing into an empty field of an existing record needs the owner's decision (default no).
* Opt-in per tenant and CRM; human-confirmed mapping (ambiguous or missing writes nothing); preview;
  per-batch approval; idempotent write key; one create attempt; a lost reply is reconciled by a
  read-only marker search; revoke stops writes at once.
* Content: public, delivered, gate-passed findings only; linted and scanned.

## Consequences

Salesforce and HubSpot adapters raise NotConfigured until apps are registered ([VERIFY]).
Proven by `test_ws7_crm.py`.
