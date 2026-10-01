# ADR-009 — Self-hosted Langfuse + OpenTelemetry (no SaaS LLM tracing)

Date: 2026-10-01
Status: Accepted

## Context
Prompts contain client material and scraped content. Sending them to a SaaS tracing vendor
would expand the sub-processor list and contradict the residency promise (v1 defect D7).

## Decision
LLM prompt/output traces stay inside the VPC via self-hosted Langfuse. Metrics and scrubbed
spans go to the observability platform through OpenTelemetry with a structured-logging field
allow-list; prompts and document text are never logged (verified by planted canary strings).

## Consequences
We operate one more service; the sub-processor register stays short and honest.

## Revisit if
Self-hosted tracing operations cost exceeds the value by Phase 2 — then re-evaluate SaaS
tracing with zero-retention contracts for metadata only.
