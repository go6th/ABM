-- Mail and drive sync state (2026-10-23): what the last sync of a per-user connection did, so
-- the page and the scheduler can tell a finished sync from a partial one or a failure without
-- marking a healthy grant broken. A transient failure (the provider down, a refresh that timed
-- out) keeps the connection active and records a fixed error code; only a revoked or changed
-- grant marks it broken (broken_reason, unchanged). ``cursor`` (0024) now carries the per-source
-- incremental cursor as a small JSON object. Additive: five nullable columns.

ALTER TABLE connector_connections
   ADD COLUMN last_sync_status text,
   ADD COLUMN last_error text,
   ADD COLUMN last_error_at timestamptz,
   ADD COLUMN last_sync_listed integer,
   ADD COLUMN last_sync_matched integer;
ALTER TABLE connector_connections
   ADD CONSTRAINT ck_connector_connections_sync_status CHECK (
      last_sync_status IS NULL OR last_sync_status IN ('ok', 'partial', 'failed')),
   ADD CONSTRAINT ck_connector_connections_last_error CHECK (
      last_error IS NULL OR last_error ~ '^[a-z_]{1,40}$'),
   ADD CONSTRAINT ck_connector_connections_sync_counts CHECK (
      (last_sync_listed IS NULL OR last_sync_listed >= 0)
      AND (last_sync_matched IS NULL OR last_sync_matched >= 0));

-- cursor (0024) and last_synced_at (0059) are already granted; the sync writes the new state.
GRANT UPDATE (last_sync_status, last_error, last_error_at, last_sync_listed, last_sync_matched)
   ON connector_connections TO app_rw;
