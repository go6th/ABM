-- Reverse 0059_maildrive_sync: drop the sync lookup and the grants it added.
REVOKE DELETE ON connector_items FROM app_rw;
REVOKE UPDATE (last_synced_at) ON connector_connections FROM app_rw;
DROP FUNCTION maildrive_connections_due(timestamptz);
