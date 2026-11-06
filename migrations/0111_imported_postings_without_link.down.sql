-- Undo migration 0111: the imported postings without an address go, the strict rule returns.
DELETE FROM job_postings WHERE url IS NULL;
ALTER TABLE job_postings DROP CONSTRAINT ck_job_postings_url;
ALTER TABLE job_postings ADD CONSTRAINT ck_job_postings_url CHECK (url IS NOT NULL AND url ~ '^https?://');
