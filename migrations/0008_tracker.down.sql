-- Rollback of 0008_tracker.sql: drops what it adds, in reverse order.
DROP TRIGGER IF EXISTS check_run_completion_trg ON runs;
DROP FUNCTION IF EXISTS check_run_completion;

DROP TRIGGER IF EXISTS coverage_row_events_seq_trg ON coverage_row_events;
DROP FUNCTION IF EXISTS set_event_seq;

DROP TABLE IF EXISTS coverage_row_events;
DROP TABLE IF EXISTS coverage_rows;
DROP TABLE IF EXISTS run_coverage_meta;
