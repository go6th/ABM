# ADR-0003: Starter-code defects and known limitations

- Status: Accepted as a defect record; fixes and gaps tracked separately
- Source: platform skill `references/13-starter-code-and-known-defects.md`

## Fixed in the starter and retained

1. Parallel LangGraph branches reduce `spent_usd` rather than writing an unreduced shared value.
2. Tenant and public-reader RLS policies name their roles; this prevents permissive-policy OR behavior from exposing private facts to the public renderer.

## Progress on known defects

1. **Paid-call duplicate dispatch:** Phase 0 implementation now adds a per-run worker lease and a durable per-call intent (`migrations/0003_data_layer_v1.sql`, `src/agents/runner.py`, `src/agents/graph.py`). An unresolved intent blocks automatic retry. This is at-most-once dispatch, not exactly-once provider execution. A killed-process spike and PostgreSQL validation remain blocked on local database tooling; see ADR-0004.
2. **Idempotency-Key/body mismatch:** still open. The API returns the earlier run on a repeated key without validating the new request body. Keep its existing strict expected-failure test.

## Consequences

Do not claim exactly-once billing. A provider call that times out or whose worker dies after dispatch but before response persistence remains unresolved and needs reconciliation; automatic retries are blocked to avoid accidental double billing.
