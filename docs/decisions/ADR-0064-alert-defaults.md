# ADR-0064: Alert rules, defaults and subscribe-up (D-74)

- Status: Proposed
- Date: 2026-10-07
- Deciders: TODO (product owner)

## Context

The ten rules come from `assets/alert_rules.yaml`; the roadmap adds facility expansion and vendor
customer story monitors but no rules; the roadmap says users subscribe up, not opt out.

## Decision

* The registry is the ten sourced rules plus `facility_expansion` and `vendor_customer_story`
  (digest, `status: proposal`), each with the signals it watches and a grade floor.
* A new user gets the weekly digest only; immediate rules, Slack and a threshold are switched on
  by the user; a bulk "subscribe to all" is explicit and audited.
* Caps (proposals): one tier-one news alert per account a day; 3 per user an hour, 10 a day; above
  5 immediate alerts a tenant a day, batch into the digest; mute and snooze per account. Every
  non-delivery is recorded with its reason; events are never deleted.

## Consequences

Proven by `test_ws5_alerting.py` (every rule fires on its scenario and stays quiet otherwise).
