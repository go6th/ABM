-- Rollback of 0036 (privacy).
DROP TABLE IF EXISTS suppression_entries;
DROP TABLE IF EXISTS dsar_events;
DROP TABLE IF EXISTS dsar_requests;
DROP TABLE IF EXISTS tombstones;
DROP TABLE IF EXISTS retention_actions;
DROP TABLE IF EXISTS retention_runs;
