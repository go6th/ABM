# ADR-014 — Vector store: pgvector in the primary database (no separate vector service)

Date: 2026-10-01
Status: Accepted

## Context
Pinecone/Milvus/OpenSearch were considered (v1 rejected Pinecone for VPC egress, defect D7).
A second datastore would need its own isolation, billing and backup story.

## Decision
Keep vectors in Postgres via pgvector (ADR-004): `halfvec(1536)`, hash partitions per
tenant, HNSW + iterative scan, hybrid retrieval with tsvector and rerank. Isolation reuses
the RLS + partition model already proven by T10; backups follow the database.

## Consequences
One fewer system to operate; HNSW memory is planned per instance class. Very large single
tenants may eventually need a dedicated partition or host — that is the bridge/silo tier.

## Revisit if
Measured p95 retrieval latency at 10M+ chunks misses the internal SLO, or V3 verification
shows the managed pgvector build missing required features.
