-- WP 1.4: broker connector ledger, per-run lease and in-graph budget (expand only).
-- Fixes known defect 3 (skill reference 13): a per-run lease admits one worker per run, and an
-- insert-only intent log records each paid attempt BEFORE the provider is called, so a crash or a
-- second worker can be detected and reconciled. See docs/decisions/ADR-0006-run-lease-and-ledger.md.
-- No existing column is dropped or altered; tool_calls stays insert-only for app_rw.

-- 1. Call record columns on the existing ledger (all nullable or defaulted).
--    tool_calls.cost_usd keeps its NOT NULL meaning: dollars CHARGED to the run budget for the
--    call (conservative reserve when the true cost is unknown). The provider's own dollar cost is
--    provider_cost_usd and is NULL when unknown. Credits are a separate column (defect 4).
ALTER TABLE tool_calls
   ADD COLUMN provider_code      text,
   ADD COLUMN operation          text,
   ADD COLUMN route              text,
   ADD COLUMN fell_back_from     text,
   ADD COLUMN credits            numeric(14,4),
   ADD COLUMN provider_cost_usd  numeric(14,6),
   ADD COLUMN cost_source        text,
   ADD COLUMN http_status        integer,
   ADD COLUMN latency_ms         integer,
   ADD COLUMN attempt            integer,
   ADD COLUMN bytes              bigint,
   ADD COLUMN possible_duplicate boolean NOT NULL DEFAULT false,
   ADD COLUMN response_ref       text,
   -- provider identity is a code, never a vendor name
   ADD CONSTRAINT ck_tool_calls_provider_code CHECK (provider_code ~ '^p_[a-z]+$'),
   ADD CONSTRAINT ck_tool_calls_fell_back_from CHECK (fell_back_from ~ '^p_[a-z]+$'),
   ADD CONSTRAINT ck_tool_calls_cost_source
      CHECK (cost_source IN ('documented', 'provider_reported', 'credits_only', 'unknown'));

-- 2. Budget columns on the run (dollars and call count; credits are per call, never summed here).
ALTER TABLE account_runs
   ADD COLUMN budget_usd      numeric(12,4),
   ADD COLUMN spent_usd       numeric(14,6) NOT NULL DEFAULT 0,
   ADD COLUMN tool_calls_used integer NOT NULL DEFAULT 0,
   ADD COLUMN tool_call_cap   integer,
   ADD CONSTRAINT ck_account_runs_spent_nonnegative CHECK (spent_usd >= 0),
   ADD CONSTRAINT ck_account_runs_budget_positive CHECK (budget_usd > 0),
   ADD CONSTRAINT ck_account_runs_calls_nonnegative CHECK (tool_calls_used >= 0),
   ADD CONSTRAINT ck_account_runs_call_cap_positive CHECK (tool_call_cap > 0);

-- 3. Per-run lease: one worker executes a run's paid calls at a time. epoch increases on every
--    change of owner and fences writes from a worker that lost the lease.
CREATE TABLE run_leases (
   run_id       uuid NOT NULL,
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   owner        text NOT NULL,
   epoch        bigint NOT NULL,
   expires_at   timestamptz NOT NULL,
   heartbeat_at timestamptz NOT NULL,
   created_at   timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_run_leases PRIMARY KEY (run_id),
   CONSTRAINT fk_run_leases_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_run_leases_epoch_positive CHECK (epoch >= 1),
   CONSTRAINT ck_run_leases_owner_not_blank CHECK (btrim(owner) <> '')
);
ALTER TABLE run_leases ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_leases FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON run_leases TO app_rw
   USING      (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE ON run_leases TO app_rw;     -- no DELETE

-- 4. Intent log: one row per attempt event, insert-only. 'started' is written (with the budget
--    reservation, in the same transaction) BEFORE the provider is called; 'done', 'failed' or
--    'abandoned' closes the attempt. 'submitted' records a provider job id for async jobs so a
--    resumed worker polls the same job instead of starting a second one.
CREATE TABLE tool_call_events (
   id                 bigserial NOT NULL,
   tenant_id          uuid NOT NULL DEFAULT app_tenant(),
   run_id             uuid NOT NULL,
   args_hash          text NOT NULL,
   attempt            integer NOT NULL,
   event              text NOT NULL,
   owner              text NOT NULL,
   lease_epoch        bigint NOT NULL,
   provider_code      text,
   operation          text,
   route              text,
   fell_back_from     text,
   reserved_usd       numeric(14,6),
   charged_usd        numeric(14,6),
   credits            numeric(14,4),
   provider_cost_usd  numeric(14,6),
   cost_source        text,
   http_status        integer,
   latency_ms         integer,
   bytes              bigint,
   possible_duplicate boolean NOT NULL DEFAULT false,
   error_class        text,
   provider_job_id    text,
   created_at         timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_tool_call_events PRIMARY KEY (id),
   CONSTRAINT uq_tool_call_events_run_id_args_hash_attempt_event UNIQUE (run_id, args_hash, attempt, event),
   CONSTRAINT fk_tool_call_events_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_tool_call_events_attempt_positive CHECK (attempt >= 1),
   CONSTRAINT ck_tool_call_events_event
      CHECK (event IN ('started', 'submitted', 'done', 'failed', 'abandoned')),
   CONSTRAINT ck_tool_call_events_provider_code CHECK (provider_code ~ '^p_[a-z]+$'),
   CONSTRAINT ck_tool_call_events_fell_back_from CHECK (fell_back_from ~ '^p_[a-z]+$'),
   CONSTRAINT ck_tool_call_events_started_reserves
      CHECK (event <> 'started' OR reserved_usd IS NOT NULL),
   CONSTRAINT ck_tool_call_events_terminal_charges
      CHECK (event NOT IN ('done', 'failed', 'abandoned') OR charged_usd IS NOT NULL),
   CONSTRAINT ck_tool_call_events_cost_source
      CHECK (cost_source IN ('documented', 'provider_reported', 'credits_only', 'unknown'))
);
CREATE INDEX ix_tool_call_events_run_id_args_hash ON tool_call_events (run_id, args_hash, attempt);
ALTER TABLE tool_call_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE tool_call_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tool_call_events TO app_rw
   USING      (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON tool_call_events TO app_rw;       -- insert-only: no UPDATE, no DELETE
GRANT USAGE ON SEQUENCE tool_call_events_id_seq TO app_rw;
