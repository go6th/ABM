-- Migration 0074: document retrieval for Ask (audit 2026-10-15).
-- Ask now searches the client's own passages (documents.retrieve): keyword search on
-- document_passages (already indexed, 0052) and meaning-based vector search on chunks.
-- 1. chunks had no account index and no index on its English tsv.
CREATE INDEX IF NOT EXISTS ix_chunks_tenant_account ON chunks (tenant_id, account_id);
CREATE INDEX IF NOT EXISTS ix_chunks_tsv ON chunks USING gin (tsv);
-- 2. A second vector per chunk from an on-server transformer (bge-small-en-v1.5, 384 dims;
-- owner decision 2026-10-15) used only for retrieval. The existing `embedding` (hashing
-- vectors) stays: the leak gate's threshold was tuned for it. NULL until a chunk is embedded
-- (new documents at indexing time, existing ones filled in by Ask, bounded per question).
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS semantic_embedding halfvec(384);
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS semantic_model text;
-- the runtime fills the retrieval vector of older chunks in place: these two columns only
GRANT UPDATE (semantic_embedding, semantic_model) ON chunks TO app_rw;
DO $$
BEGIN
  FOR i IN 0..15 LOOP
    EXECUTE format(
      'CREATE INDEX IF NOT EXISTS ix_chunks_p%s_semantic ON chunks_p%s USING hnsw'
      ' (semantic_embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)', i, i);
  END LOOP;
END
$$;
