-- Undo migration 0085: the no-progress slice counter.
ALTER TABLE account_runs DROP CONSTRAINT IF EXISTS ck_account_runs_no_progress_slices;
ALTER TABLE account_runs DROP COLUMN IF EXISTS no_progress_slices;
