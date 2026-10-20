-- Undo migration 0079: the spend view, the Ask turn columns and the batch flag.
DROP VIEW IF EXISTS llm_spend_daily;
ALTER TABLE chat_turns DROP COLUMN IF EXISTS billed_model_id;
ALTER TABLE chat_turns DROP CONSTRAINT IF EXISTS ck_chat_turns_surface;
ALTER TABLE chat_turns DROP COLUMN IF EXISTS surface;
ALTER TABLE model_reply_log DROP COLUMN IF EXISTS batch;
