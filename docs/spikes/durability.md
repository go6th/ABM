# Durability spike 2: execution and acceptance protocol

**Status: Local spike passed; stakeholder sign-off pending.** No real provider was called. The acceptance test uses PostgreSQL checkpoints and a deterministic local paid-call stub.

Run the two-node graph with a deterministic recorded provider stub. The paid-call counter must be a database-backed test counter, not an in-memory counter. Persist the input, graph/checkpoint version, tool/argument hash, intent state, ledger response, and final graph state.

1. Start a run, pause after the durable paid response is recorded but before the node checkpoint, kill the worker process, and resume.
2. Repeat the kill/resume cycle five times. Assert the paid stub is invoked once and the graph reaches the same terminal result.
3. Kill after an intent is committed but before the provider response is persisted. Assert retry is blocked as ambiguous and the call is not automatically dispatched again.
4. Start two workers for the same run concurrently. Assert one obtains the lease and only one worker advances the graph.
5. Run the same tests after a fresh migration and through forced RLS as `app_rw`.

Observed locally: five independent runs were each killed after branch A's response and tool-call ledger row committed but before its graph node checkpoint. Each resumed two-branch graph reached `COMPLETED`, and a forced-RLS PostgreSQL invocation counter recorded exactly one stub invocation for each branch (two per run). The ambiguous-outcome test also confirmed that a committed intent with a lost response blocks automatic redispatch. The concurrent-worker test confirmed that a second worker cannot acquire the active run lease.

Command: `PGPORT=5433 PGPASSWORD=dev PGADMINUSER=postgres TEST_DB=ai APP_RW_PASSWORD=local-dev-only MIGRATOR_DATABASE_URL='postgresql://postgres:dev@localhost:5433/ai' CHECKPOINT_DATABASE_URL='postgresql://app_rw:local-dev-only@localhost:5433/ai' uv run --frozen pytest -q tests/integration/test_graph_and_ledger.py::test_checkpoint_resume_after_five_mid_fanout_process_kills` — passed (1 test; five kill/resume cycles). The full database suite and coverage gate also passed subsequently.

The current safety property is **at-most-once dispatch with manual reconciliation**, not exactly-once provider execution. The local spike supports keeping LangGraph plus Inngest as the C1 default; it does not prove provider billing behavior. If future restore tests fail, evaluate Temporal as the C1 fallback. Neither Temporal nor a workflow checkpoint can alone establish exactly-once external billing: provider idempotency or an authoritative reconciliation API is still needed.
