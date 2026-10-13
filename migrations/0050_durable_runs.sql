-- Durable runs (2026-10-13): why a run stopped, and discovery of runs whose worker died.

-- A redacted, bounded description of the stop (error class and message; keys and tokens are
-- removed in code before the write). finished_reason keeps the short code.
ALTER TABLE account_runs ADD COLUMN stop_detail text;
ALTER TABLE account_runs ADD CONSTRAINT ck_account_runs_stop_detail
   CHECK (stop_detail IS NULL OR char_length(stop_detail) <= 600);

-- The orphan sweeper needs the active runs whose lease is long gone, across tenants. It gets
-- identifiers only through this narrow function; each run is then ended under its own tenant
-- context (tenant_txn), so RLS still applies to every row it touches.
-- reviewed: SECURITY DEFINER returns (tenant_id, run_id) identifiers only, of runs in an active
-- state whose last sign of life (the lease heartbeat, or creation when no lease exists) is
-- older than the given time and whose lease has expired; search_path pinned; EXECUTE granted to app_rw only.
CREATE FUNCTION runs_orphaned(stale_before timestamptz)
   RETURNS TABLE (tenant_id uuid, run_id uuid)
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT r.tenant_id, r.id FROM account_runs r
   LEFT JOIN run_leases l ON l.run_id = r.id
   WHERE r.status IN ('RUNNING', 'DISTILLING', 'VERIFYING')
     AND (l.expires_at IS NULL OR l.expires_at < stale_before)
     AND COALESCE(l.heartbeat_at, r.created_at) < stale_before
   ORDER BY r.created_at, r.id
$$;
REVOKE ALL ON FUNCTION runs_orphaned(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION runs_orphaned(timestamptz) TO app_rw;

-- Write-ahead for model calls: a 'started' row is logged before every model request, so a call
-- the process died in is still visible (with its run, task and model). A started row is never a
-- reuse hit (reuse reads served_from = 'model' only). The CHECK is WIDENED.
ALTER TABLE model_reply_log DROP CONSTRAINT ck_model_reply_log_served;
ALTER TABLE model_reply_log ADD CONSTRAINT ck_model_reply_log_served
   CHECK (served_from IN ('started', 'model', 'run_replay', 'cache')) NOT VALID;
ALTER TABLE model_reply_log VALIDATE CONSTRAINT ck_model_reply_log_served;
