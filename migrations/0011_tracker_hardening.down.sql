-- Rollback of 0011_tracker_hardening.sql.
REVOKE USAGE ON SEQUENCE coverage_row_events_id_seq FROM app_rw;
ALTER TABLE account_runs
   DROP CONSTRAINT ck_account_runs_run_input_object,
   DROP COLUMN run_input;
ALTER TABLE coverage_rows
   DROP CONSTRAINT ck_coverage_rows_attempts,
   DROP CONSTRAINT ck_coverage_rows_no_progress,
   DROP CONSTRAINT ck_coverage_rows_proof_calls,
   DROP CONSTRAINT ck_coverage_rows_detail_len,
   DROP COLUMN exhausted,
   DROP COLUMN no_progress_count,
   DROP COLUMN proof_calls,
   DROP COLUMN detail;
