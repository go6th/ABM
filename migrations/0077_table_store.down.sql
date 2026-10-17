-- Undo migration 0077: the table store and the passage metadata.
DROP TABLE IF EXISTS document_table_rows;
DROP TABLE IF EXISTS document_tables;
ALTER TABLE document_passages DROP COLUMN IF EXISTS metadata;
