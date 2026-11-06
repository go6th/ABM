# ADR-0134: the monitoring product (refresh, signals, alerts) stays in src/ but is marked not wired

- Status: Accepted
- Date: 2026-11-03
- Deciders: owner (fix prompt F, task 2)

## Context

`src/refresh` (1,442 lines), `src/signals` (745) and `src/alerts` (1,115) are the Phase 4
monitoring product: shelf-life policy, probe and escalation, deltas and history, monitor intake
and scoring, alert rules, subscriptions, outbox and digest. They are built and tested
(`tests/phase4/`), but no production path drives them:

* `refresh.probe.run_probe` is called only by tests (`tests/phase4/test_ws10_simulation.py`,
  `tests/phase4/test_ws9_hardening.py`).
* The retention and alert-digest scheduler ticks were removed (ADR-0132); no tick, task route or
  worker job reaches the probe, the outbox or the digest.
* Open items OI-P4-1 to OI-P4-7 (`docs/open-items.md`) record the missing production wiring:
  the probe's grading port is wired to fakes, real monitor, Slack, email and CRM adapters raise
  `NotConfigured`, webhook payloads are not handed to the extractor.

The 9 routes in `src/account_intelligence/api/routes/signals.py` are reachable, but only for a
client whose `monitoring` feature flag is on (ADR-0112: "product work in progress"). That is a
plan to ship, not a dead path, so the packages are not parked.

## Decision

1. The three packages stay. Each `__init__.py` docstring starts with `NOT WIRED — see ADR-0134`.
2. The only modules outside the three packages allowed to import them are, today:
   * `account_intelligence.api.routes.signals` (behind the `monitoring` flag): `alerts.feedback`,
     `alerts.rules`, `alerts.subscriptions`, `refresh.history`;
   * `crm_writeback.payloads` (behind the `crm` flag, ADR-0112): `alerts.lint`,
     `alerts.sensitive` (pure wording checks);
   * `documents.workspace_docx` (the workspace zip import, 2026-11-03): `signals.events`
     (`import_events` writes the Account Summary's signal rows as `signal_events`; no tick,
     alert or channel runs).
3. `tests/guardrails/test_monitoring_not_wired.py` fails when any other module under `src/`
   imports `refresh`, `signals` or `alerts`, or when an allowed importer is not named in this ADR.
   Wiring the product (a scheduler tick, a worker job, a replica screen) therefore means updating
   this ADR and the test's allow-list in the same change.
4. The tables they own are not dropped (migrations are append-only); `config/ownership.yaml` marks
   them as written only by the not-wired monitoring product.

## Consequences

* A reader of `src/` sees at the package boundary that this code does not run in production.
* Shipping monitoring needs, at minimum: the probe's grading port bound to the production grading
  (OI-P4-1), a scheduled entry point (a bounded tick like ADR-0108), real channel adapters
  (OI-P4-5), and a screen. Until then the `monitoring` flag should stay off for every client.
* If the owner decides monitoring will not ship, the three packages, their tests and the 9
  `signals.py` routes are parked together into `unwanted-things/` (a later prompt), and this ADR
  is superseded.
