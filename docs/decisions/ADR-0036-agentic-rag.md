# ADR-0036: Agentic RAG subsystem (GROUNDED blueprint)

- Status: Accepted
- Date: 2026-10-05

## Decision

The question-answering path is a new `account_intelligence.rag` subpackage, not
an extension of the research workflow. It follows the platform's established
discipline: ports for every external dependency (embedder, dense/sparse
retrievers, knowledge graph, LLM, guardrail, cache, context store), pure and
deterministic fusion/ranking/evaluation logic, fail-closed defaults (missing
adapter raises `NotConfigured`, missing ACL drops the chunk, missing tenant
context returns zero rows), and a small checkpoint state that carries ids and
references only — chunk text and prompt payloads never enter the LangGraph
checkpoint.

Retrieval is hybrid by construction: a dense arm and a BM25 sparse arm are
fused with reciprocal rank fusion, metadata filters are applied before ranking,
and a deterministic lexical re-ranker blends with the fused score. Relationship
queries route to the knowledge graph (multi-hop traversal with explicit evidence
paths); fact lookups stay on the vector path. A reflection node critiques the
retrieved context (relevance, completeness, quality) and loops back to retrieval
with a refined query while passes remain; a circuit breaker caps total passes
(OWASP LLM10). Answers are structured JSON with inline `[Source: id]`
citations; orphan citations are detected and loop back, never emitted.

## Consequences

Every threshold lives in `config/rag.yaml`. The semantic cache is advisory: a
hit bypasses retrieval and generation, a miss or stale entry falls through.
Guardrails run before the generator and fail closed when unconfigured. Retrieval
enforces the caller's ACL (tenant, roles, clearance) before any text reaches the
context window (LLM02). Evaluation metrics (P@K, R@K, MRR, NDCG, groundedness,
answer relevance, CF, RA) are pure functions so CI can gate on them without a
model.
