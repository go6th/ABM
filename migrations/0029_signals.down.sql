-- Rollback of 0029_signals.sql (development only; production never contracts in the same release).
DROP TABLE IF EXISTS intake_drops;
DROP TABLE IF EXISTS signal_event_links;
DROP TABLE IF EXISTS signal_event_evidence;
DROP TABLE IF EXISTS signal_events;
DROP TABLE IF EXISTS webhook_receipts;
DROP TABLE IF EXISTS monitor_runs;
DROP TABLE IF EXISTS monitor_configs;
