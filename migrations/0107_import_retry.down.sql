-- Down 0107: the import parts lose their retry time.
DROP INDEX IF EXISTS ix_import_run_parts_retry;
ALTER TABLE import_run_parts DROP COLUMN IF EXISTS next_attempt_at;
