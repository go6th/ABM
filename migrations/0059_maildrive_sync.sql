-- Mail and drive sync: the per-user connections due for a sync. Identifiers only; the sync then
-- reads and writes inside each tenant's own transaction under row level security.

-- reviewed: SECURITY DEFINER returns (tenant_id, connection_id) pairs only, of active personal
-- (owner_user_id set) mail and drive connections not synced since p_before; search_path pinned;
-- EXECUTE granted to app_rw only.
CREATE FUNCTION maildrive_connections_due(p_before timestamptz)
   RETURNS TABLE (tenant_id uuid, connection_id uuid)
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT c.tenant_id, c.id FROM connector_connections c
    WHERE c.status = 'active' AND c.owner_user_id IS NOT NULL
      AND c.kind IN ('gmail', 'outlook', 'google_drive', 'onedrive', 'sharepoint')
      AND (c.last_synced_at IS NULL OR c.last_synced_at < p_before)
    ORDER BY c.last_synced_at NULLS FIRST, c.id
    LIMIT 500
$$;
REVOKE ALL ON FUNCTION maildrive_connections_due(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION maildrive_connections_due(timestamptz) TO app_rw;

-- The sync records when a connection was last synced; Disconnect deletes what it synced.
GRANT UPDATE (last_synced_at) ON connector_connections TO app_rw;
GRANT DELETE ON connector_items TO app_rw;
