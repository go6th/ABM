-- Rollback of 0015_credit_cap.sql (a failed deploy only: restoring the narrower cost_source
-- CHECKs fails, by design, once a 'prepulled' ledger row exists).
ALTER TABLE tool_call_events DROP CONSTRAINT ck_tool_call_events_cost_source;
ALTER TABLE tool_call_events ADD CONSTRAINT ck_tool_call_events_cost_source
   CHECK (cost_source IN ('documented', 'provider_reported', 'credits_only', 'unknown'));
ALTER TABLE tool_calls DROP CONSTRAINT ck_tool_calls_cost_source;
ALTER TABLE tool_calls ADD CONSTRAINT ck_tool_calls_cost_source
   CHECK (cost_source IN ('documented', 'provider_reported', 'credits_only', 'unknown'));
DROP TABLE spend_defects;
DROP FUNCTION spend_runs_to_check(timestamptz);
DROP VIEW run_spend_by_provider;
DROP TABLE prepulled_files;
DROP TABLE spend_reports;
DROP TABLE balance_snapshots;
DROP TABLE run_cap_events;
DROP TABLE run_cap_versions;
ALTER TABLE account_runs
   DROP CONSTRAINT ck_account_runs_spent_within_cap,
   DROP CONSTRAINT ck_account_runs_capped_has_budget,
   DROP CONSTRAINT ck_account_runs_sample_ops,
   DROP CONSTRAINT ck_account_runs_unreconciled,
   DROP CONSTRAINT ck_account_runs_overshoot,
   DROP CONSTRAINT ck_account_runs_cap_version,
   DROP COLUMN unreconciled_usd, DROP COLUMN cap_overshoot_usd, DROP COLUMN attended,
   DROP COLUMN cap_sample_ops, DROP COLUMN cap_version, DROP COLUMN cap_required;
