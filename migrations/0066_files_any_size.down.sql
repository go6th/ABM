-- Reverse files of any size (passages numbered past 2^31 must be removed first).
DROP FUNCTION IF EXISTS document_parts_orphaned(timestamptz);
DROP TABLE IF EXISTS document_parts;
ALTER TABLE documents
   DROP CONSTRAINT IF EXISTS ck_documents_parts_total,
   DROP COLUMN IF EXISTS indexed_through,
   DROP COLUMN IF EXISTS parts_total;
DROP INDEX IF EXISTS ix_chunks_doc_seq;
DELETE FROM chunks WHERE passage_seq > 2147483647;
DELETE FROM document_redactions WHERE passage_seq > 2147483647;
DELETE FROM document_passages WHERE seq > 2147483647;
ALTER TABLE chunks ALTER COLUMN passage_seq TYPE integer;
ALTER TABLE document_redactions ALTER COLUMN passage_seq TYPE integer;
ALTER TABLE document_passages ALTER COLUMN seq TYPE integer;
ALTER TABLE platform_defaults
   DROP CONSTRAINT IF EXISTS ck_platform_defaults_max_file_bytes,
   DROP COLUMN IF EXISTS max_file_bytes;
