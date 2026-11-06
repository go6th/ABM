"""NOT WIRED — see ADR-0134 (docs/decisions/ADR-0134-monitoring-product-not-wired.md): no
scheduler tick, task route or worker job reaches this package; only the flag-gated routes in
``account_intelligence.api.routes.signals`` (and ``crm_writeback.payloads`` for ``alerts``) import
it, and ``tests/guardrails/test_monitoring_not_wired.py`` keeps it that way.

The refresh engine (Phase 4): shelf-life policy, slots, probe and escalation, deltas, history
and the diff-first Summary. It schedules and decides; facts, grades, source codes, statuses and
money go through their single owners (grading engine, allocator, tracker, ledger)."""
