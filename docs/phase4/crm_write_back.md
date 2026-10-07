# CRM write-back: contract and customer setup guide (CW1 to CW5, D-77)

## Contract
* We only CREATE: notes, tasks (preparation to-dos for the owner) and account snapshot records. We
  never update, upsert, merge or delete anything in your CRM; the client has no such method.
* Content: public findings from delivered, quality-gated output, with source codes. Never internal
  material, sensitive findings, provider names, credit counts or costs.
* Every write is requested, previewed, approved per batch, created once (idempotent key), and
  listed with its events under Settings > CRM.

## Setup (customer admin)
1. Install the snapshot object or note type in your CRM sandbox first (the field list is in the
   preview; [VERIFY] V-P4-CRM-1).
2. Settings > CRM: choose Salesforce or HubSpot and Enable (off by default). The connection itself
   needs an OAuth app registration (Blocked until registered).
3. For each account, confirm its CRM record id. An account with no confirmed mapping is never
   written to; an ambiguous match writes nothing.
4. Preview, request, approve. Revoke stops all writes at once.
