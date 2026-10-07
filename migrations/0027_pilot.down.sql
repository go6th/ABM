-- Rollback of 0027_pilot.sql (development only; production never contracts in the same release).
DROP FUNCTION IF EXISTS pilot_aggregate(timestamptz);
DROP TABLE IF EXISTS usefulness_events;
DROP TABLE IF EXISTS run_feedback;
