-- Token review (2026-10-20), item 19: one view of model spend per day by surface (Ask, chat,
-- research run), task kind and model, with the cache-read share, so the largest line on the
-- invoice can be found. Item 8: research calls sent through the Message Batches API are priced
-- at half and flagged. Ask turns now store every token class, their priced cost and the model
-- that was paid (a reply the answer did not use was still paid).
-- Every change is additive: new nullable or defaulted columns and a new view.

ALTER TABLE model_reply_log ADD COLUMN batch boolean NOT NULL DEFAULT false;

ALTER TABLE chat_turns ADD COLUMN surface text NOT NULL DEFAULT 'chat';
ALTER TABLE chat_turns ADD CONSTRAINT ck_chat_turns_surface CHECK (surface IN ('chat', 'ask'));
ALTER TABLE chat_turns ADD COLUMN billed_model_id text;
-- Ask turns are the ones with a structured answer (migration 0053); under the migrator's RLS
-- this may touch no row, which only leaves older Ask turns counted as chat
UPDATE chat_turns SET surface = 'ask' WHERE answer_json IS NOT NULL;

-- security_invoker: the caller's RLS applies (a tenant sees only its own spend). Research rows
-- are the paid model calls of the reply log (served_from 'model'; reuse and replay cost
-- nothing); Ask and chat rows are their turns except the open reservations.
CREATE VIEW llm_spend_daily WITH (security_invoker = true) AS
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
