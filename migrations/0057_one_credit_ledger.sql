-- One credit truth: the client's monthly pool and its append-only ledger (credit_entries) carry
-- every credit movement. Every research run belongs to one research job; the old wallet tables
-- stay for history and become read-only to the application role.

-- A library add is a credit movement at zero cost: it is recorded as a job that used nothing.
ALTER TABLE research_jobs DROP CONSTRAINT ck_research_jobs_kind;
ALTER TABLE research_jobs ADD CONSTRAINT ck_research_jobs_kind
   CHECK (kind IN ('new_research', 'web_refresh', 'scheduled_refresh', 'file_analysis', 'library_add'));
ALTER TABLE research_jobs DROP CONSTRAINT ck_research_jobs_scope;
ALTER TABLE research_jobs ADD CONSTRAINT ck_research_jobs_scope
   CHECK (scope IN ('jobs', 'people', 'full', 'file', 'topic', 'library'));
ALTER TABLE credit_entries DROP CONSTRAINT ck_credit_entries_purpose;
ALTER TABLE credit_entries ADD CONSTRAINT ck_credit_entries_purpose
   CHECK (purpose IN ('new_research', 'web_refresh', 'scheduled_refresh', 'file_analysis', 'library_add'));

-- One job per run: settlement finds the job from the run.
CREATE UNIQUE INDEX uq_research_jobs_run ON research_jobs (tenant_id, run_id) WHERE run_id IS NOT NULL;

-- Why a job stopped early when it reached its credits (shown with the job and its tracker rows).
ALTER TABLE research_jobs ADD COLUMN stop_reason text;
ALTER TABLE research_jobs ADD CONSTRAINT ck_research_jobs_stop_reason
   CHECK (stop_reason IS NULL OR stop_reason IN ('credit_cap_reached'));

-- The wallet tables are history now: the application may read them, never change them.
REVOKE INSERT, UPDATE ON wallets FROM app_rw;
REVOKE INSERT, UPDATE ON wallet_ledger FROM app_rw;

-- reviewed: SECURITY DEFINER returns tenant_id identifiers only, of tenants with credit entries or
-- research jobs since p_since (the nightly ledger check then reads each tenant inside its own
-- transaction under row level security); search_path pinned; EXECUTE granted to app_rw only.
CREATE FUNCTION tenants_with_credit_activity(p_since timestamptz)
   RETURNS TABLE (tenant_id uuid)
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT e.tenant_id FROM credit_entries e WHERE e.at >= p_since
   UNION
   SELECT j.tenant_id FROM research_jobs j WHERE j.created_at >= p_since OR j.finished_at >= p_since
$$;
REVOKE ALL ON FUNCTION tenants_with_credit_activity(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION tenants_with_credit_activity(timestamptz) TO app_rw;
