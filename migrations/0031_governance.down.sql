-- Rollback of 0031_governance.sql (development only; production never contracts in the same release).
DROP TABLE IF EXISTS throttle_events;
DROP TABLE IF EXISTS monitoring_usage;
DROP TABLE IF EXISTS monitoring_budget_versions;
