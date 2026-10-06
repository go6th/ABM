-- Rollback of 0017 (contract direction; only for a failed deploy before the tables hold run
-- history: unit events, limit events, notifications and upload intents are records and are never
-- dropped once used).
DROP FUNCTION IF EXISTS governor_active(text, timestamptz);
DROP FUNCTION IF EXISTS governor_signal(text, text, timestamptz, double precision);
DROP FUNCTION IF EXISTS governor_release(uuid, timestamptz);
DROP FUNCTION IF EXISTS governor_heartbeat(uuid, timestamptz, double precision);
DROP FUNCTION IF EXISTS governor_acquire(text, uuid, text, integer, timestamptz, double precision,
   double precision, integer, integer, double precision, integer, integer);
DROP FUNCTION IF EXISTS governor_configure(text, integer, integer, timestamptz);
DROP TABLE IF EXISTS prepulled_intent_events;
DROP TABLE IF EXISTS prepulled_intents;
DROP TABLE IF EXISTS notification_deliveries;
DROP TABLE IF EXISTS notification_outbox;
DROP TABLE IF EXISTS spend_limit_events;
DROP TABLE IF EXISTS provider_waiters;
DROP TABLE IF EXISTS provider_leases;
DROP TABLE IF EXISTS provider_limit_events;
DROP TABLE IF EXISTS provider_limits;
DROP TABLE IF EXISTS work_unit_events;
DROP TABLE IF EXISTS work_units;
ALTER TABLE account_runs DROP CONSTRAINT IF EXISTS ck_account_runs_awaiting_kind;
ALTER TABLE account_runs DROP COLUMN IF EXISTS awaiting_kind;
