-- Migration 0011: tracker hardening and run input (expand only; no column dropped or altered).
-- Intent (spec build_tracker_state_machine.md, findings SC-3 and T9 in docs/audit/SPEC-COMPLIANCE.md):
--   1. coverage_rows: internal-only detail (T9, length capped), the number of calls in the proof
--      (gap-loop progress, audit F-12), the no-progress count and the exhausted flag (5.1).
--   2. coverage_row_events: the owning stage is already a column; nothing to add.
--   3. account_runs.run_input: the run's scope as given at creation (lanes, pursuing client), read by
--      the tracker's RunInputPort (T4) and the stages. Identifiers and choices only, never content.
-- Rollback: 0011_tracker_hardening.down.sql.

ALTER TABLE coverage_rows
   ADD COLUMN detail text,
   ADD COLUMN proof_calls integer NOT NULL DEFAULT 0,
   ADD COLUMN no_progress_count integer NOT NULL DEFAULT 0,
   ADD COLUMN exhausted boolean NOT NULL DEFAULT false,
   ADD CONSTRAINT ck_coverage_rows_detail_len CHECK (char_length(detail) <= 500),
   ADD CONSTRAINT ck_coverage_rows_proof_calls CHECK (proof_calls >= 0),
   ADD CONSTRAINT ck_coverage_rows_no_progress CHECK (no_progress_count >= 0),
   ADD CONSTRAINT ck_coverage_rows_attempts CHECK (attempts >= 0);

ALTER TABLE account_runs
   ADD COLUMN run_input jsonb NOT NULL DEFAULT '{}'::jsonb,
   ADD CONSTRAINT ck_account_runs_run_input_object CHECK (jsonb_typeof(run_input) = 'object');

-- 0008 granted INSERT on coverage_row_events but not the id sequence, so app_rw could not write an
-- event at all (found by the tracker DB tests). Insert-only grants stay: no UPDATE, no DELETE.
GRANT USAGE ON SEQUENCE coverage_row_events_id_seq TO app_rw;
