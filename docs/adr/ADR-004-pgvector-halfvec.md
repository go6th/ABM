# ADR-004 — pgvector halfvec(1536) with hash partitions

Date: 2026-10-01
Status: Accepted

## Context
Phase B needs per-tenant vector search inside the same isolation model. Plain RLS+HNSW
post-filtering under-recalls for small tenants (v1 defect D12).

## Decision
Store embeddings as pgvector `halfvec(1536)` (16-bit float, HNSW-capable to 4000 dims),
hash-partition the chunks table by `tenant_id` (16 partitions), enable iterative index
scans, and fuse vector + tsvector retrieval with rerank. Embeddings are requested at
1536 dims from the embedding model's dimension option.

## Consequences
One datastore and one isolation model; HNSW memory becomes an instance-sizing concern
(~6–10 KB per chunk). Recall is measured per tenant-size band, not globally.

## Revisit if
V3 (verification register) shows the Aurora pgvector version lacks `halfvec` or iterative
scan; then fall back to `vector(1536)` with partitions and measure the memory cost.
