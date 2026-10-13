-- Narrow the served_from CHECK back (started rows must be removed first; scratch database only).

ALTER TABLE model_reply_log DROP CONSTRAINT ck_model_reply_log_served;
ALTER TABLE model_reply_log ADD CONSTRAINT ck_model_reply_log_served
   CHECK (served_from IN ('model', 'run_replay', 'cache'));
-- Remove the orphan discovery function and the stop detail column.

DROP FUNCTION IF EXISTS runs_orphaned(timestamptz);
ALTER TABLE account_runs DROP CONSTRAINT IF EXISTS ck_account_runs_stop_detail;
ALTER TABLE account_runs DROP COLUMN IF EXISTS stop_detail;
