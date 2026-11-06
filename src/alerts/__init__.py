"""NOT WIRED — see ADR-0134 (docs/decisions/ADR-0134-monitoring-product-not-wired.md): no
scheduler tick, task route or worker job reaches this package; only the flag-gated routes in
``account_intelligence.api.routes.signals`` (and ``crm_writeback.payloads`` for ``alerts``) import
it, and ``tests/guardrails/test_monitoring_not_wired.py`` keeps it that way.

Alerting (Phase 4 WS5): rules registry, subscriptions, caps, templates, sensitive wording, the
outbox and channels, freshness and usefulness. No model: every word is a template."""
