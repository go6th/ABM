-- Rollback of 0074.
DO $$
BEGIN
  FOR i IN 0..15 LOOP
    EXECUTE format('DROP INDEX IF EXISTS ix_chunks_p%s_semantic', i);
  END LOOP;
END
$$;
REVOKE UPDATE (semantic_embedding, semantic_model) ON chunks FROM app_rw;
ALTER TABLE chunks DROP COLUMN IF EXISTS semantic_model;
ALTER TABLE chunks DROP COLUMN IF EXISTS semantic_embedding;
DROP INDEX IF EXISTS ix_chunks_tsv;
DROP INDEX IF EXISTS ix_chunks_tenant_account;
