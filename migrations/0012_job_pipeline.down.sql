-- Rollback of 0012_job_pipeline.sql.
DROP TABLE job_pipeline_runs;
DROP TABLE job_posting_observations;
DROP TABLE job_posting_classifications;
DROP TABLE job_posting_platform_sentences;
DROP INDEX ix_job_postings_account_id_posting_key;
ALTER TABLE job_postings
   DROP CONSTRAINT ck_job_postings_text_sha256,
   DROP CONSTRAINT ck_job_postings_status_source,
   DROP CONSTRAINT ck_job_postings_class,
   DROP CONSTRAINT ck_job_postings_url,
   DROP COLUMN hostile, DROP COLUMN undated, DROP COLUMN weak_key, DROP COLUMN title_only,
   DROP COLUMN platforms, DROP COLUMN lanes, DROP COLUMN posting_class, DROP COLUMN full_text_read,
   DROP COLUMN text_complete, DROP COLUMN text_sha256, DROP COLUMN text_ref, DROP COLUMN status_source,
   DROP COLUMN first_seen, DROP COLUMN posted_at, DROP COLUMN location, DROP COLUMN title,
   DROP COLUMN other_urls, DROP COLUMN url;
