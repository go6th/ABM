-- Rollback of 0076.
DROP INDEX IF EXISTS ix_documents_import_pending;
ALTER TABLE documents DROP COLUMN IF EXISTS imported_at;
