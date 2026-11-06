-- Undo migration 0110: the two states become rejected rows and the counter goes.
UPDATE documents SET status = 'rejected', reject_reason = COALESCE(reject_reason, 'not_uploaded')
 WHERE status IN ('scan_failed', 'abandoned');

ALTER TABLE documents DROP CONSTRAINT ck_documents_rejected_reason;
ALTER TABLE documents ADD CONSTRAINT ck_documents_rejected_reason
   CHECK ((status = 'rejected') = (reject_reason IS NOT NULL));

ALTER TABLE documents DROP CONSTRAINT ck_documents_status;
ALTER TABLE documents ADD CONSTRAINT ck_documents_status CHECK (status IN ('awaiting_upload',
   'quarantined', 'scanning', 'rejected', 'parsing', 'parsed', 'needs_account', 'indexing',
   'indexed', 'failed'));

ALTER TABLE documents DROP CONSTRAINT ck_documents_scan_attempts;
ALTER TABLE documents DROP COLUMN scan_attempts;
