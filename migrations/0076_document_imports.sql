-- Migration 0076: documents.imported_at (documents.deliverables, 2026-10-16).
-- When a document's rows were last read for the standing deliverable workbooks (job postings,
-- people, technologies); NULL until then. The scheduled sweep backfills the documents that were
-- indexed before the import existed, a few per pass, and marks each one read, rows or not.
ALTER TABLE documents ADD COLUMN imported_at timestamptz;
CREATE INDEX ix_documents_import_pending ON documents (tenant_id, created_at)
   WHERE status = 'indexed' AND account_id IS NOT NULL AND imported_at IS NULL;
