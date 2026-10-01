# ADR-002 — LangGraph checkpoint as durability source of truth

Date: 2026-10-01
Status: Accepted

## Context
Runs are multi-hour, call paid APIs and must survive kills without paying twice. The
checkpoint must be the single retry/durability owner to avoid retry storms.

## Decision
LangGraph Postgres checkpointer is the source of truth for run state. `thread_id` is
`'<tenant_uuid>:<run_uuid>'` so a prefix RLS policy can guard library-owned tables with no
join to application tables. The runner uses one dedicated, non-pooled connection per run.

## Consequences
One owner per failure class: LangGraph retries nodes, infrastructure retries slices,
the ledger prevents double billing. We operate the checkpoint lifecycle (GC after terminal + 7 days).

## Revisit if
Spike 2 (T11) cannot prove resume-after-kill with exactly-once ledger entries; then the
outer loop (not the checkpoint) moves to Temporal per the T4 conflict register.
