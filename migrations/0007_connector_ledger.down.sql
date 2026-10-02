DROP TABLE IF EXISTS tool_call_events;
DROP TABLE IF EXISTS run_leases;
ALTER TABLE account_runs
   DROP COLUMN IF EXISTS budget_usd, DROP COLUMN IF EXISTS spent_usd,
   DROP COLUMN IF EXISTS tool_calls_used, DROP COLUMN IF EXISTS tool_call_cap;
ALTER TABLE tool_calls
   DROP COLUMN IF EXISTS provider_code, DROP COLUMN IF EXISTS operation, DROP COLUMN IF EXISTS route,
   DROP COLUMN IF EXISTS fell_back_from, DROP COLUMN IF EXISTS credits,
   DROP COLUMN IF EXISTS provider_cost_usd, DROP COLUMN IF EXISTS cost_source,
   DROP COLUMN IF EXISTS http_status, DROP COLUMN IF EXISTS latency_ms, DROP COLUMN IF EXISTS attempt,
   DROP COLUMN IF EXISTS bytes, DROP COLUMN IF EXISTS possible_duplicate,
   DROP COLUMN IF EXISTS response_ref;
