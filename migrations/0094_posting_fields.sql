-- Migration 0094: job posting fields for the reference workbook and incremental re-research
-- (expand only; no column dropped or altered). job_postings gains the feed's own last-seen date
-- and seniority word (both printed by the Job Postings file) and the incremental flag: true when
-- the posting was first seen after the account's previous delivered research, false when it was
-- carried over from it, null for a first research. job_pipeline_runs records which delivered run
-- a research carried its postings from and how many it carried. The row_json record keeps the
-- same fields; the columns exist for queries. Rollback: 0094_posting_fields.down.sql.
--
-- Numbering: this change was written as 0087 and 0088 on the overnight branch while the
-- account-delete (0087) and account-bundle (0088) migrations went live on staging first, so it
-- was renumbered to follow the staging head (0093). It is IDEMPOTENT: every column and the
-- constraint are added only when missing, so it applies on a database that never ran it (staging)
-- and on one that already holds any piece of it.

ALTER TABLE job_postings
   ADD COLUMN IF NOT EXISTS last_seen          date,
   ADD COLUMN IF NOT EXISTS seniority          text,
   ADD COLUMN IF NOT EXISTS new_since_previous boolean;
COMMENT ON COLUMN job_postings.last_seen IS 'The feed''s own last-seen date of the posting (null when the source gave none).';
COMMENT ON COLUMN job_postings.seniority IS 'The feed''s seniority word as given (null when the source gave none).';
COMMENT ON COLUMN job_postings.new_since_previous IS 'Incremental re-research: true = first seen after the previous delivered research, false = carried from it, null = first research.';

ALTER TABLE job_pipeline_runs
   ADD COLUMN IF NOT EXISTS previous_run_id uuid,
   ADD COLUMN IF NOT EXISTS carried         integer NOT NULL DEFAULT 0;
DO $$
BEGIN
   IF NOT EXISTS (
      SELECT 1 FROM pg_constraint
       WHERE conname = 'ck_job_pipeline_runs_carried'
         AND conrelid = 'job_pipeline_runs'::regclass
   ) THEN
      ALTER TABLE job_pipeline_runs
         ADD CONSTRAINT ck_job_pipeline_runs_carried CHECK (carried >= 0);
   END IF;
END
$$;
COMMENT ON COLUMN job_pipeline_runs.previous_run_id IS 'The delivered run whose postings this research carried without a pull, read or extractor call (null for a first research).';
COMMENT ON COLUMN job_pipeline_runs.carried IS 'How many postings were carried from previous_run_id.';
