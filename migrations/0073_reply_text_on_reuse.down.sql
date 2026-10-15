-- Rollback of 0073: reuse rows lose their text again, then the narrower CHECK returns.
UPDATE model_reply_log SET reply_text = NULL, reply_model = NULL WHERE served_from = 'cache';
ALTER TABLE model_reply_log DROP CONSTRAINT ck_model_reply_log_text;
ALTER TABLE model_reply_log ADD CONSTRAINT ck_model_reply_log_text CHECK (
   (served_from = 'model') OR (reply_text IS NULL AND reply_model IS NULL));
