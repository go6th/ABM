-- Research cache (2026-10-13): the model reply log and an index for cross-run reuse of
-- finished provider calls. Both are tenant scoped; nothing here crosses a tenant.

CREATE TABLE model_reply_log (
   id                 bigserial NOT NULL,
   tenant_id          uuid NOT NULL DEFAULT app_tenant(),
   run_id             uuid NOT NULL,
   cache_key          text NOT NULL,
   task_kind          text NOT NULL,
   tier               text NOT NULL,
   model              text NOT NULL,
   served_from        text NOT NULL,
   input_tokens       integer NOT NULL DEFAULT 0,
   output_tokens      integer NOT NULL DEFAULT 0,
   cache_read_tokens  integer NOT NULL DEFAULT 0,
   cache_write_tokens integer NOT NULL DEFAULT 0,
   cost_usd           numeric(14,6),
   cost_known         boolean NOT NULL DEFAULT false,
   reply_text         text,
   reply_model        text,
   created_at         timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_model_reply_log PRIMARY KEY (id),
   CONSTRAINT ck_model_reply_log_served CHECK (served_from IN ('model', 'run_replay', 'cache')),
   CONSTRAINT ck_model_reply_log_tokens CHECK (input_tokens >= 0 AND output_tokens >= 0
      AND cache_read_tokens >= 0 AND cache_write_tokens >= 0),
   CONSTRAINT ck_model_reply_log_cost CHECK (cost_usd IS NULL OR cost_usd >= 0),
   CONSTRAINT ck_model_reply_log_text CHECK (
      (served_from = 'model') OR (reply_text IS NULL AND reply_model IS NULL))
);
ALTER TABLE model_reply_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_reply_log FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON model_reply_log TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON model_reply_log TO app_rw;
GRANT USAGE, SELECT ON SEQUENCE model_reply_log_id_seq TO app_rw;
CREATE INDEX ix_model_reply_log_tenant_id_cache_key_created_at
   ON model_reply_log (tenant_id, cache_key, created_at DESC) WHERE served_from = 'model';
CREATE INDEX ix_model_reply_log_tenant_id_run_id ON model_reply_log (tenant_id, run_id);

-- cross-run lookup of a finished call by its arguments hash (connectors/cache.py)
CREATE INDEX ix_tool_calls_tenant_id_args_hash_created_at
   ON tool_calls (tenant_id, args_hash, created_at DESC);

-- a call adopted from an earlier run of the tenant is a done call at zero cost with cost_source
-- 'cache'. The two CHECKs are WIDENED (every value they accepted stays accepted).
ALTER TABLE tool_calls DROP CONSTRAINT ck_tool_calls_cost_source;
ALTER TABLE tool_calls ADD CONSTRAINT ck_tool_calls_cost_source CHECK (cost_source IN
   ('documented', 'provider_reported', 'credits_only', 'unknown', 'prepulled', 'cache')) NOT VALID;
ALTER TABLE tool_calls VALIDATE CONSTRAINT ck_tool_calls_cost_source;
ALTER TABLE tool_call_events DROP CONSTRAINT ck_tool_call_events_cost_source;
ALTER TABLE tool_call_events ADD CONSTRAINT ck_tool_call_events_cost_source CHECK (cost_source IN
   ('documented', 'provider_reported', 'credits_only', 'unknown', 'prepulled', 'cache')) NOT VALID;
ALTER TABLE tool_call_events VALIDATE CONSTRAINT ck_tool_call_events_cost_source;
