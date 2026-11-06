-- Ingestion hardening PR6: the tokens a document used, on its row, and the embedding tokens in the
-- per-tenant model usage view the admin side reads (llm_spend_daily, migration 0079).
-- Every change is additive: defaulted columns, one nullable column, and extra rows in the view.

ALTER TABLE documents ADD COLUMN embedding_tokens bigint NOT NULL DEFAULT 0;
ALTER TABLE documents ADD COLUMN analysis_input_tokens bigint NOT NULL DEFAULT 0;
ALTER TABLE documents ADD COLUMN analysis_output_tokens bigint NOT NULL DEFAULT 0;
ALTER TABLE documents ADD COLUMN analysis_run_id uuid;
ALTER TABLE documents ADD CONSTRAINT ck_documents_tokens
   CHECK (embedding_tokens >= 0 AND analysis_input_tokens >= 0 AND analysis_output_tokens >= 0);

-- Same view as 0079 plus a fourth source: the embedding batches of the file model call log
-- (task 'embed_passages'), surface 'file_embedding'. The local embedder costs nothing, so the line
-- carries tokens and the cost the call log recorded (0 for the local embedder).
CREATE OR REPLACE VIEW llm_spend_daily WITH (security_invoker = true) AS
SELECT c.tenant_id,
       c.day,
       c.surface,
       c.task_kind,
       c.model,
       c.batch,
       COUNT(*) AS calls,
       SUM(c.input_tokens) AS input_tokens,
       SUM(c.output_tokens) AS output_tokens,
       SUM(c.cache_read_tokens) AS cache_read_tokens,
       SUM(c.cache_write_tokens) AS cache_write_tokens,
       SUM(c.cost_usd) AS cost_usd,
       CASE WHEN SUM(c.input_tokens + c.cache_read_tokens + c.cache_write_tokens) > 0
            THEN ROUND(SUM(c.cache_read_tokens)::numeric
                       / SUM(c.input_tokens + c.cache_read_tokens + c.cache_write_tokens), 4)
       END AS cache_read_share
  FROM (
        SELECT l.tenant_id,
               (l.created_at AT TIME ZONE 'UTC')::date AS day,
               'research'::text AS surface,
               l.task_kind,
               l.model,
               l.batch,
               l.input_tokens::bigint AS input_tokens,
               l.output_tokens::bigint AS output_tokens,
               l.cache_read_tokens::bigint AS cache_read_tokens,
               l.cache_write_tokens::bigint AS cache_write_tokens,
               COALESCE(l.cost_usd, 0)::numeric AS cost_usd
          FROM model_reply_log l
         WHERE l.served_from = 'model'
        UNION ALL
        SELECT t.tenant_id,
               (t.created_at AT TIME ZONE 'UTC')::date,
               t.surface,
               t.surface,
               COALESCE(t.billed_model_id, t.model_id),
               false,
               t.input_tokens::bigint,
               t.output_tokens::bigint,
               t.cache_read_tokens::bigint,
               t.cache_write_tokens::bigint,
               t.cost_usd::numeric
          FROM chat_turns t
         WHERE t.status <> 'reserved'
        UNION ALL
        SELECT e.tenant_id,
               (e.created_at AT TIME ZONE 'UTC')::date,
               'file_embedding'::text,
               e.task,
               e.model,
               false,
               e.input_tokens::bigint,
               e.output_tokens::bigint,
               0::bigint,
               0::bigint,
               COALESCE(e.cost_usd, 0)::numeric
          FROM research_file_model_calls e
         WHERE e.task = 'embed_passages' AND e.served_from = 'model'
       ) c
 GROUP BY c.tenant_id, c.day, c.surface, c.task_kind, c.model, c.batch;
GRANT SELECT ON llm_spend_daily TO app_rw;
