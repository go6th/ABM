-- Undo migration 0109: held uploads go back to 'parsed' (account NULL or bound: the old waiting
-- state), then the status list loses 'needs_account'.
UPDATE documents SET status = 'parsed' WHERE status = 'needs_account';
ALTER TABLE documents DROP CONSTRAINT ck_documents_status;
ALTER TABLE documents ADD CONSTRAINT ck_documents_status CHECK (status IN ('awaiting_upload',
   'quarantined', 'scanning', 'rejected', 'parsing', 'parsed', 'indexing', 'indexed', 'failed'));
