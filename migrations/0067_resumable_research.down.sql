-- Reverse resumable research.
DROP FUNCTION IF EXISTS runs_stalled(timestamptz);
DELETE FROM notifications WHERE kind = 'research_stalled';
ALTER TABLE notifications DROP CONSTRAINT ck_notifications_kind;
ALTER TABLE notifications ADD CONSTRAINT ck_notifications_kind CHECK (kind IN ('refresh_finished',
   'research_finished', 'research_failed', 'waiting_credit_limit', 'access_expiring',
   'library_added', 'limit_request_approved', 'limit_request_declined', 'research_limit_reached'));
ALTER TABLE research_jobs DROP COLUMN IF EXISTS resume_from;
ALTER TABLE account_runs DROP COLUMN IF EXISTS stalled_at, DROP COLUMN IF EXISTS progress_at;
DROP INDEX IF EXISTS ix_tool_call_events_query;
ALTER TABLE tool_call_events DROP COLUMN IF EXISTS query_key;
ALTER TABLE platform_defaults
   DROP CONSTRAINT IF EXISTS ck_platform_defaults_run_dollar_cap,
   DROP CONSTRAINT IF EXISTS ck_platform_defaults_repeat_call_limit,
   DROP CONSTRAINT IF EXISTS ck_platform_defaults_stall_after,
   DROP COLUMN IF EXISTS run_dollar_cap_usd,
   DROP COLUMN IF EXISTS repeat_call_limit,
   DROP COLUMN IF EXISTS stall_after_s;
