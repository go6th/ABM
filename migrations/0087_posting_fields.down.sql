-- Undo migration 0087: the posting fields for the reference workbook and incremental re-research.
ALTER TABLE job_pipeline_runs DROP CONSTRAINT IF EXISTS ck_job_pipeline_runs_carried;
ALTER TABLE job_pipeline_runs DROP COLUMN IF EXISTS carried;
ALTER TABLE job_pipeline_runs DROP COLUMN IF EXISTS previous_run_id;
ALTER TABLE job_postings DROP COLUMN IF EXISTS new_since_previous;
ALTER TABLE job_postings DROP COLUMN IF EXISTS seniority;
ALTER TABLE job_postings DROP COLUMN IF EXISTS last_seen;
