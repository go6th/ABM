# ADR-0055: Connector set and the narrowest scope (D-65)

- Status: Proposed
- Date: 2026-10-07
- Deciders: TODO (product owner)

## Context

D-08 orders Grain, then CRM and Drive; ARCH names neither Gmail nor call notes; the roadmap lists
Salesforce, HubSpot, Drive, Gmail and call notes. Connectors can expose sensitive material.

## Decision

* Framework plus Google Drive, Salesforce and HubSpot first. Built: the OAuth flow, the exact scope
  table, consent, quarantine, the access log and the Drive ancestry and bounded fetch logic, all
  behind ports and tested against fakes. Not built: any real provider HTTP client (no OAuth app is
  registered; the production ports raise `NotConfigured`) and the Salesforce and HubSpot fetch
  (scope tables only). Gmail and call notes are flagged off and raise `NotConfigured`.
* Per-source consent by a tenant admin (step-up), OAuth with PKCE bound to tenant, user and
  connection; the granted scopes must equal `config/connectors.yaml` exactly (scope widening
  refused). Drive reads only inside admin-named folders, by **resolved ancestry**: shortcuts,
  multiple parents, shared drives and unknown ancestry are denied unless allow-listed; a client
  folder that surfaces is quarantined until the admin clears it.
* Every access is a `connector_access_log` row; fetch is page- and quota-bounded and resumable; no
  write call exists; content lands in quarantine only (parsing and embedding are Phase B).

## Consequences

Scopes and endpoints are [VERIFY] against the providers' current docs. Proven by
`tests/phase3/test_ws6_connectors.py` (ancestry oracle, OAuth binding, scope, bounds, token
canaries).
