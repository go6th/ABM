-- Rollback of 0025_metering.sql (development only; production never contracts in the same release).
DROP TABLE IF EXISTS usage_thresholds;
DROP TABLE IF EXISTS usage_counters;
DROP TABLE IF EXISTS plan_assignments;
DROP TABLE IF EXISTS plan_limits;
DROP TABLE IF EXISTS usage_events;
