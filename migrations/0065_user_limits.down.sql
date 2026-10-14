-- Reverse the per-user limits.
DELETE FROM notifications WHERE kind IN ('limit_request_approved', 'limit_request_declined', 'research_limit_reached');
ALTER TABLE notifications DROP CONSTRAINT ck_notifications_kind;
ALTER TABLE notifications ADD CONSTRAINT ck_notifications_kind CHECK (kind IN ('refresh_finished',
   'research_finished', 'research_failed', 'waiting_credit_limit', 'access_expiring', 'library_added'));
DROP TABLE IF EXISTS limit_requests;
DROP TABLE IF EXISTS usage_periods;
DROP TABLE IF EXISTS user_limits;
ALTER TABLE client_settings
   DROP COLUMN IF EXISTS research_limit_default,
   DROP COLUMN IF EXISTS chat_limit_default,
   DROP COLUMN IF EXISTS limit_reset;
ALTER TABLE platform_defaults
   DROP COLUMN IF EXISTS research_limit_default,
   DROP COLUMN IF EXISTS chat_limit_default,
   DROP COLUMN IF EXISTS limit_reset;
