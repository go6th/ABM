-- Migration 0075: a research continues itself (end-to-end automation, 2026-10-16).
-- The scheduled sweep now continues a research that stalled or lost its worker the way the Resume
-- button does (replica.auto_resume); the owner is told with a notification of a new kind,
-- research_resumed, so the constraint on notification kinds grows by that one value.
ALTER TABLE notifications DROP CONSTRAINT ck_notifications_kind;
ALTER TABLE notifications ADD CONSTRAINT ck_notifications_kind CHECK (kind IN ('refresh_finished',
   'research_finished', 'research_failed', 'waiting_credit_limit', 'access_expiring',
   'library_added', 'limit_request_approved', 'limit_request_declined', 'research_limit_reached',
   'research_stalled', 'research_resumed'));
