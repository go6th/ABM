-- Remove the model reply log and the cross-run lookup index; narrow the cost_source CHECKs back
-- (rows with cost_source 'cache' must be gone first; the deploy runbook deletes none, so this
-- downgrade is for a scratch database only).

ALTER TABLE tool_call_events DROP CONSTRAINT ck_tool_call_events_cost_source;
ALTER TABLE tool_call_events ADD CONSTRAINT ck_tool_call_events_cost_source CHECK (cost_source IN
   ('documented', 'provider_reported', 'credits_only', 'unknown', 'prepulled'));
ALTER TABLE tool_calls DROP CONSTRAINT ck_tool_calls_cost_source;
ALTER TABLE tool_calls ADD CONSTRAINT ck_tool_calls_cost_source CHECK (cost_source IN
   ('documented', 'provider_reported', 'credits_only', 'unknown', 'prepulled'));
DROP INDEX IF EXISTS ix_tool_calls_tenant_id_args_hash_created_at;
DROP TABLE IF EXISTS model_reply_log;
