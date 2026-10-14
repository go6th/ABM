-- Reverse 0055_schedule_lookups: drop the scheduler's two cross-tenant lookups.
DROP FUNCTION tenants_with_waiting_jobs();
DROP FUNCTION due_scheduled_refreshes(timestamptz);
