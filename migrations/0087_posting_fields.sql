-- Migration 0087: job posting fields for the reference workbook and incremental re-research
-- (expand only; no column dropped or altered). job_postings gains the feed's own last-seen date
-- and seniority word (both printed by the Job Postings file) and the incremental flag: true when
-- the posting was first seen after the account's previous delivered research, false when it was
-- carried over from it, null for a first research. job_pipeline_runs records which delivered run
-- a research carried its postings from and how many it carried. The row_json record keeps the
-- same fields; the columns exist for queries. Rollback: 0087_posting_fields.down.sql.

ALTER TABLE job_postings
   ADD COLUMN last_seen          date,
   ADD COLUMN seniority          text,
   ADD COLUMN new_since_previous boolean;
COMMENT ON COLUMN job_postings.last_seen IS 'The feed''s own last-seen date of the posting (null when the source gave none).';
COMMENT ON COLUMN job_postings.seniority IS 'The feed''s seniority word as given (null when the source gave none).';
COMMENT ON COLUMN job_postings.new_since_previous IS 'Incremental re-research: true = first seen after the previous delivered research, false = carried from it, null = first research.';

ALTER TABLE job_pipeline_runs
   ADD COLUMN previous_run_id uuid,
   ADD COLUMN carried         integer NOT NULL DEFAULT 0,
   ADD CONSTRAINT ck_job_pipeline_runs_carried CHECK (carried >= 0);
COMMENT ON COLUMN job_pipeline_runs.previous_run_id IS 'The delivered run whose postings this research carried without a pull, read or extractor call (null for a first research).';
COMMENT ON COLUMN job_pipeline_runs.carried IS 'How many postings were carried from previous_run_id.';
