-- Migration 0110 (2026-11-04, ingestion hardening PR4 part B): two new document states and a
-- scan attempt counter.
--
-- scan_failed: the malware scanner could not be reached. The file is not clean and not infected;
-- it stays in quarantine, is never read, and the sweeper scans it again (at most three attempts in
-- all). Its reject_reason ('scan_unavailable') is kept for the message shown to the user.
-- abandoned: an upload still awaiting its bytes 24 hours after it began; the daily job marks it
-- and deletes whatever object it left in quarantine.
-- reject_reason is now allowed on rejected and scan_failed rows only, and required on both.

ALTER TABLE documents ADD COLUMN scan_attempts integer NOT NULL DEFAULT 0;
ALTER TABLE documents ADD CONSTRAINT ck_documents_scan_attempts CHECK (scan_attempts >= 0);

ALTER TABLE documents DROP CONSTRAINT ck_documents_status;
ALTER TABLE documents ADD CONSTRAINT ck_documents_status CHECK (status IN ('awaiting_upload',
   'quarantined', 'scanning', 'rejected', 'scan_failed', 'abandoned', 'parsing', 'parsed',
   'needs_account', 'indexing', 'indexed', 'failed'));

ALTER TABLE documents DROP CONSTRAINT ck_documents_rejected_reason;
ALTER TABLE documents ADD CONSTRAINT ck_documents_rejected_reason CHECK (
   (status NOT IN ('rejected', 'scan_failed') OR reject_reason IS NOT NULL)
   AND (reject_reason IS NULL OR status IN ('rejected', 'scan_failed')));
