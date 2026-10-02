# Phase 0 decision log

Date: 2026-10-02
Status: Proposed; required stakeholder sign-offs are pending.

This log records decisions that can be supported by repository evidence and explicitly leaves benchmark-dependent decisions open. No September account data, broker credits or external model calls were available or used.

| Decision | Current disposition | Evidence | Missing evidence / approval |
|---|---|---|---|
| Workflow engine | Keep LangGraph Postgres checkpoints with Inngest as the reversible outer-loop default, per skill conflict C1. Do not adopt Temporal unless a future durability acceptance run fails. | Five local subprocess kill/resume cycles passed using a PostgreSQL-backed checkpoint and deterministic paid-call stub; each resumed two-branch run completed with one stub invocation per branch. Ambiguous-call blocking and a competing-worker lease also passed in `tests/integration/test_graph_and_ledger.py`. See [ADR-0004](./ADR-0004-durable-execution.md) and [durability spike](../spikes/durability.md). | Tech lead, analyst owner and product owner must review and sign off. Local stubs do not establish provider-side exactly-once billing. |
| Model split | No benchmark decision. Keep the routing layer configurable; `single` remains the proposed arm until the paired comparison is measured. | Environment-backed planner/extractor/judge tier routing and a validator for paired coverage, citation validity, cost and elapsed time are implemented in `config/model_routing.yaml` and `src/benchmarks/model_split.py`; synthetic input rejection is tested. | Supply three September account snapshots and approved measured outputs for both arms, including citation review, provider cost and elapsed time. No result row is recorded in [the benchmark protocol](../spikes/model-split.md). |
| People source | No comparison decision. `bitscale_only` remains the configuration default to avoid unapproved calls; Sumble remains configured for organizations, technology and teams in both modes. | Injectable adapter routing supports BitScale-only and paired modes, and a protocol defines fill rate and cost per usable senior person. Both modes and the Sumble invariant are tested without network access in `tests/unit/test_phase0_harnesses.py`. | Supply the same three September account snapshots, broker cost/credit records, analyst usability rubric and explicit spend approval. No result row is recorded in [the people-source protocol](../spikes/people-sources.md). |

The architecture roles remain `admin`, `seller` and `viewer`. The analyst-role question remains open; this log does not add that role.
