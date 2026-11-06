-- Migration 0111 (2026-11-05, exact copy of an uploaded workspace): a posting imported from an
-- uploaded workspace workbook may have no web address.
--
-- The owner's Job Postings sheet lists closed postings as archive records, some without a link
-- and without a requisition the careers pattern could rebuild (3 of 92 rows in the UCB package).
-- The rule "an upload comes back word for word" keeps those rows; a researched posting (any
-- other provider) still needs its address.

ALTER TABLE job_postings DROP CONSTRAINT ck_job_postings_url;
ALTER TABLE job_postings ADD CONSTRAINT ck_job_postings_url CHECK (
   (url IS NOT NULL AND url ~ '^https?://')
   OR (url IS NULL AND coalesce(row_json->>'provider_code', '') = 'file_import'));
