-- Rollback of 0075: the notifications of the new kind go, and the constraint returns to 0067's.
DELETE FROM notifications WHERE kind = 'research_resumed';
ALTER TABLE notifications DROP CONSTRAINT ck_notifications_kind;
ALTER TABLE notifications ADD CONSTRAINT ck_notifications_kind CHECK (kind IN ('refresh_finished',
   'research_finished', 'research_failed', 'waiting_credit_limit', 'access_expiring',
   'library_added', 'limit_request_approved', 'limit_request_declined', 'research_limit_reached',
   'research_stalled'));
