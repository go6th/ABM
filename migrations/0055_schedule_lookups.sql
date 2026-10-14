-- The scheduler's two cross-tenant lookups. Each returns identifiers only; the scheduler then does
-- all reading and writing inside each tenant's own transaction under row level security.

-- reviewed: SECURITY DEFINER returns (tenant_id, account_id) identifiers only, of accounts whose
-- refresh schedule is on and due at p_now and that have no research job waiting, queued or
-- running; search_path pinned; EXECUTE granted to app_rw only.
CREATE FUNCTION due_scheduled_refreshes(p_now timestamptz)
   RETURNS TABLE (tenant_id uuid, account_id uuid)
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT a.tenant_id, a.id FROM accounts a
    WHERE a.refresh_cadence IN ('3d', 'weekly', '2w', 'monthly')
      AND a.next_refresh_at IS NOT NULL AND a.next_refresh_at <= p_now
      AND NOT EXISTS (SELECT 1 FROM research_jobs j WHERE j.account_id = a.id
                       AND j.status IN ('waiting_credit_limit', 'queued', 'running'))
    ORDER BY a.next_refresh_at, a.id
    LIMIT 500
$$;
REVOKE ALL ON FUNCTION due_scheduled_refreshes(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION due_scheduled_refreshes(timestamptz) TO app_rw;

-- reviewed: SECURITY DEFINER returns tenant_id identifiers only, of tenants that have research
-- jobs waiting for credits (the scheduler retries them when the pool has room, for example on the
-- 1st); search_path pinned; EXECUTE granted to app_rw only.
CREATE FUNCTION tenants_with_waiting_jobs()
   RETURNS TABLE (tenant_id uuid)
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT DISTINCT j.tenant_id FROM research_jobs j WHERE j.status = 'waiting_credit_limit'
$$;
REVOKE ALL ON FUNCTION tenants_with_waiting_jobs() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION tenants_with_waiting_jobs() TO app_rw;
