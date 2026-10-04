# ADR-0035: credit exhaustion (D-31)

- Status: Proposed (D-31 open; conflict C16)
- Date: 2026-10-04
- Deciders: TODO (product owner)

## Context

PRD 3.5: supply a pre-pulled file rather than fall back to a weaker free source. `routing.yaml`
lists fallback providers, and the router fell back on `CreditsExhausted`.

## Decision (proposal)

- Provider credits exhausted makes the call fail with its ledger proof; the tracker maps it to
  `credits_exhausted` (Blocked, the run continues) and the operator is prompted (`Notifier`).
- The router falls back on exhaustion only when the tenant policy sets
  `allow_fallback_on_exhaustion: true` (default false, `config/spend.yaml`).
- A pre-pulled file registered for (run, provider, operation) (`spend.prepulled.register`: hash,
  provenance, record count, insert-only) is consumed by `call_operation` instead of the paid call:
  parsed by the provider's own parser, refused when its hash or record count differ, recorded in
  the ledger as a done call at zero cost with `cost_source='prepulled'` (the ledger CHECK is
  widened in 0015), counted apart from paid calls in the report. It never touches the cap.
- An operation listed as free that returns a cost is recorded in `spend_defects` and refused from
  then on (K14). None is listed free today.

## Consequences

The tracker proof of a pre-pulled read is an ordinary ledger record. Evidence:
`tests/spend/test_capguard.py`, `tests/connectors/test_router_product.py`.
