-- Research that never loses data and never pays twice (owner decision 2026-10-14, Phase 4B
-- section 4).
--
-- platform_defaults.run_dollar_cap_usd: the hard ceiling of one run's real spend, provider calls
--   and model calls together, set by the admin (default $25.00; above the per-run budget,
--   RUN_BUDGET_USD, which pauses for approval); a run that reaches it stops PARTIAL.
-- platform_defaults.repeat_call_limit: how many times one run may pay a provider for the same
--   normalised query (first pages); one more stops the run PARTIAL.
-- platform_defaults.stall_after_s: a running run with no progress for this long is stalled.
-- tool_call_events.query_key: the normalised query of a first-page call (the detector counts it).
-- account_runs.progress_at: the heartbeat, moved by every ledger event and model reply.
-- account_runs.stalled_at: set once by the sweeper; the owner is told and offered Resume.
-- research_jobs.resume_from: a job that continues a stopped run, reusing every finished pull
--   and model reply of it at no cost.

ALTER TABLE platform_defaults
   ADD COLUMN run_dollar_cap_usd numeric(10, 2) NOT NULL DEFAULT 25.00,
   ADD COLUMN repeat_call_limit integer NOT NULL DEFAULT 3,
   ADD COLUMN stall_after_s integer NOT NULL DEFAULT 1800,
   ADD CONSTRAINT ck_platform_defaults_run_dollar_cap CHECK (run_dollar_cap_usd BETWEEN 0.10 AND 10000),
   ADD CONSTRAINT ck_platform_defaults_repeat_call_limit CHECK (repeat_call_limit BETWEEN 1 AND 50),
   ADD CONSTRAINT ck_platform_defaults_stall_after CHECK (stall_after_s BETWEEN 60 AND 86400);

ALTER TABLE tool_call_events ADD COLUMN query_key text;
CREATE INDEX ix_tool_call_events_query ON tool_call_events (run_id, query_key)
   WHERE query_key IS NOT NULL AND event = 'started';

ALTER TABLE account_runs
   ADD COLUMN progress_at timestamptz,
   ADD COLUMN stalled_at timestamptz;

ALTER TABLE research_jobs ADD COLUMN resume_from uuid;

ALTER TABLE notifications DROP CONSTRAINT ck_notifications_kind;
ALTER TABLE notifications ADD CONSTRAINT ck_notifications_kind CHECK (kind IN ('refresh_finished',
   'research_finished', 'research_failed', 'waiting_credit_limit', 'access_expiring',
   'library_added', 'limit_request_approved', 'limit_request_declined', 'research_limit_reached',
   'research_stalled'));

-- Running runs that have not moved since ``stale_before`` and are not marked yet (the sweeper
-- marks each once and tells its owner).
CREATE FUNCTION runs_stalled(stale_before timestamptz)
   RETURNS TABLE (tenant_id uuid, run_id uuid)
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT r.tenant_id, r.id FROM account_runs r
   WHERE r.status IN ('RUNNING', 'DISTILLING', 'VERIFYING')
     AND r.stalled_at IS NULL
     AND COALESCE(r.progress_at, r.created_at) < stale_before
   ORDER BY r.created_at, r.id
$$;
REVOKE ALL ON FUNCTION runs_stalled(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION runs_stalled(timestamptz) TO app_rw;
