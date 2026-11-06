-- Migration 0109 (2026-11-04, ingestion hardening PR5): documents.status gains 'needs_account'.
--
-- An upload whose company could not be matched to its account (no match, ambiguous, or the
-- detected company is another domain than the account it was uploaded to) is held in this status:
-- nothing is imported or indexed until the user binds it (confirm-company). Uploads at 'parsed'
-- with no account (the old hold) are shown as needs_account by the view and are released the same
-- way, so no row is rewritten here.
ALTER TABLE documents DROP CONSTRAINT ck_documents_status;
ALTER TABLE documents ADD CONSTRAINT ck_documents_status CHECK (status IN ('awaiting_upload',
   'quarantined', 'scanning', 'rejected', 'parsing', 'parsed', 'needs_account', 'indexing',
   'indexed', 'failed'));
