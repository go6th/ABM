-- Rollback of 0013_job_gap_passes.sql.
DROP TABLE job_pipeline_passes;
ALTER TABLE job_posting_platform_sentences
   DROP CONSTRAINT ck_job_posting_platform_sentences_pass_no, DROP COLUMN pass_no;
ALTER TABLE job_posting_classifications
   DROP CONSTRAINT ck_job_posting_classifications_pass_no, DROP COLUMN pass_no;
ALTER TABLE job_pipeline_runs DROP CONSTRAINT uq_job_pipeline_runs_tenant_id_run_id;
