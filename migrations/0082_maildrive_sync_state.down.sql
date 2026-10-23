-- Undo migration 0082: the Mail and drive sync state (dropping a column drops its grant).
ALTER TABLE connector_connections DROP CONSTRAINT IF EXISTS ck_connector_connections_sync_counts;
ALTER TABLE connector_connections DROP CONSTRAINT IF EXISTS ck_connector_connections_last_error;
ALTER TABLE connector_connections DROP CONSTRAINT IF EXISTS ck_connector_connections_sync_status;
ALTER TABLE connector_connections
   DROP COLUMN IF EXISTS last_sync_matched,
   DROP COLUMN IF EXISTS last_sync_listed,
   DROP COLUMN IF EXISTS last_error_at,
   DROP COLUMN IF EXISTS last_error,
   DROP COLUMN IF EXISTS last_sync_status;
