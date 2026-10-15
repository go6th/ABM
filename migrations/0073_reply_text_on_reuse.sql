-- Migration 0073: a reused model reply keeps its text (audit 2026-10-15).
-- A resume reuses the replies of the run it resumes from model_reply_log. The reuse row was kept
-- without its text (served_from = 'cache', reply_text NULL), so resuming THAT run again found
-- nothing and paid for every reply a second time. The CHECK is WIDENED: every row it accepted
-- stays accepted; 'cache' rows may now carry the text they reused.
ALTER TABLE model_reply_log DROP CONSTRAINT ck_model_reply_log_text;
ALTER TABLE model_reply_log ADD CONSTRAINT ck_model_reply_log_text CHECK (
   (served_from IN ('model', 'cache')) OR (reply_text IS NULL AND reply_model IS NULL)) NOT VALID;
ALTER TABLE model_reply_log VALIDATE CONSTRAINT ck_model_reply_log_text;
