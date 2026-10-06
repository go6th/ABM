-- Migration 0019: observability (expand only). Spec docs/prompts/build_observability.md section 5.
-- down_revision 0018.
--
-- ops_cost_rollups: DERIVED and recomputable from the insert-only ledger (documented as NOT a
-- ledger, O6): an idempotent upsert keyed by (run, provider code, route, cost source). Tenant
-- scoped, forced RLS. ops_rollup_watermarks: how far the rollup job got (derived, mutable).
-- slo_snapshots: insert-only SLI snapshots of the platform (no tenant column: platform-wide
-- counts only, no content). stuck_run_observations and canary_probe_results: insert-only,
-- tenant scoped. public_reader has no grant on any table here (operator views only, O15).
-- Rollback: 0019_observability.down.sql.

CREATE TABLE ops_cost_rollups (
   id                 bigserial NOT NULL,
   tenant_id          uuid NOT NULL DEFAULT app_tenant(),
   run_id             uuid NOT NULL,
   account_id         uuid,
   provider_code      text NOT NULL,
   route              text NOT NULL,
   cost_source        text NOT NULL,
   plan_tier          text NOT NULL,
   calls              integer NOT NULL,
   cost_micros        bigint NOT NULL,
   unknown_cost_calls integer NOT NULL,
   refreshed_at       timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_ops_cost_rollups PRIMARY KEY (id),
   CONSTRAINT fk_ops_cost_rollups_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT uq_ops_cost_rollups_key UNIQUE (run_id, provider_code, route, cost_source),
   CONSTRAINT ck_ops_cost_rollups_counts CHECK (calls >= 0 AND cost_micros >= 0
      AND unknown_cost_calls >= 0)
);
ALTER TABLE ops_cost_rollups ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops_cost_rollups FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON ops_cost_rollups TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE ON ops_cost_rollups TO app_rw;
GRANT USAGE ON SEQUENCE ops_cost_rollups_id_seq TO app_rw;

CREATE TABLE ops_rollup_watermarks (
   tenant_id   uuid NOT NULL DEFAULT app_tenant(),
   job         text NOT NULL,
   watermark   timestamptz NOT NULL,
   updated_at  timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_ops_rollup_watermarks PRIMARY KEY (tenant_id, job),
   CONSTRAINT fk_ops_rollup_watermarks_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id)
);
ALTER TABLE ops_rollup_watermarks ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops_rollup_watermarks FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON ops_rollup_watermarks TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE ON ops_rollup_watermarks TO app_rw;

CREATE TABLE slo_snapshots (
   id              bigserial NOT NULL,
   slo             text NOT NULL,
   window_s        integer NOT NULL,
   good            bigint,
   total           bigint,
   burn_long       double precision,
   burn_short      double precision,
   state           text NOT NULL,
   as_of           timestamptz NOT NULL,
   definition_hash text NOT NULL,
   CONSTRAINT pk_slo_snapshots PRIMARY KEY (id),
   CONSTRAINT ck_slo_snapshots_state CHECK (state IN ('ok', 'burning', 'no_data')),
   CONSTRAINT uq_slo_snapshots UNIQUE (slo, window_s, as_of)
);
GRANT SELECT, INSERT ON slo_snapshots TO app_rw;
GRANT USAGE ON SEQUENCE slo_snapshots_id_seq TO app_rw;

CREATE TABLE stuck_run_observations (
   id          bigserial NOT NULL,
   tenant_id   uuid NOT NULL DEFAULT app_tenant(),
   run_id      uuid NOT NULL,
   state       text NOT NULL,
   reason      text NOT NULL,
   age_bucket  text NOT NULL,
   at          timestamptz NOT NULL,
   CONSTRAINT pk_stuck_run_observations PRIMARY KEY (id),
   CONSTRAINT fk_stuck_run_observations_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_stuck_run_observations_reason CHECK (reason IN ('queued_too_long',
      'no_activity', 'approval_past_deadline'))
);
ALTER TABLE stuck_run_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE stuck_run_observations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON stuck_run_observations TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON stuck_run_observations TO app_rw;
GRANT USAGE ON SEQUENCE stuck_run_observations_id_seq TO app_rw;

CREATE TABLE canary_probe_results (
   id          bigserial NOT NULL,
   tenant_id   uuid NOT NULL DEFAULT app_tenant(),
   probe       text NOT NULL,
   leaks       integer NOT NULL,
   at          timestamptz NOT NULL,
   CONSTRAINT pk_canary_probe_results PRIMARY KEY (id),
   CONSTRAINT fk_canary_probe_results_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_canary_probe_results_leaks CHECK (leaks >= 0)
);
ALTER TABLE canary_probe_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE canary_probe_results FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON canary_probe_results TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON canary_probe_results TO app_rw;
GRANT USAGE ON SEQUENCE canary_probe_results_id_seq TO app_rw;
