-- Undo migration 0108: the view goes back to 0079 (without the embedding lines); the columns go.
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
       ) c
 GROUP BY c.tenant_id, c.day, c.surface, c.task_kind, c.model, c.batch;
GRANT SELECT ON llm_spend_daily TO app_rw;

ALTER TABLE documents DROP CONSTRAINT IF EXISTS ck_documents_tokens;
ALTER TABLE documents DROP COLUMN IF EXISTS analysis_run_id;
ALTER TABLE documents DROP COLUMN IF EXISTS analysis_output_tokens;
ALTER TABLE documents DROP COLUMN IF EXISTS analysis_input_tokens;
ALTER TABLE documents DROP COLUMN IF EXISTS embedding_tokens;
