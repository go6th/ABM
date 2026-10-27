-- Slices that ended on their timer without moving the run's checkpoint (2026-10-27): the
-- staging research of 2026-10-26 ran 43 slices of 1650 s in a row with the same node pending,
-- 20 hours of worker time and judge calls, because the slice ceiling is off while the research
-- caps are off and nothing else counted slices that went nowhere. The runner resets this on a
-- slice that moved the checkpoint and ends the run PARTIAL (reason no_progress) past
-- workflow_v1.yaml max_no_progress_slices. Additive: one counter column.
ALTER TABLE account_runs ADD COLUMN no_progress_slices integer NOT NULL DEFAULT 0;
ALTER TABLE account_runs ADD CONSTRAINT ck_account_runs_no_progress_slices
   CHECK (no_progress_slices >= 0);
