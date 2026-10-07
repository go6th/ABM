-- Migration 0031: monitoring cost governance (Phase 4 WS6; expand only). Spec
-- docs/prompts/build_phase4.md s5, CG1 to CG3, D-79. down_revision 0030.
--
-- monitoring_budget_versions: insert-only operator-set monthly allowances (plan units); absent
--   means the plan default from config/budgets.yaml.
-- monitoring_usage: DERIVED and mutable (documented): the units used per tenant and month, raised
--   only by one conditional update that cannot pass the allowance; a new month is a new row (no
--   rollover). Recomputable from monitor_runs.
-- throttle_events: insert-only, unique (period, step): the 80% alert and each ladder step happen
--   once per tenant and period.
-- Rollback: 0031_governance.down.sql.

CREATE TABLE monitoring_budget_versions (
   tenant_id        uuid NOT NULL DEFAULT app_tenant(),
   version          integer NOT NULL,
   allowance_units  bigint NOT NULL,
   set_by           text NOT NULL,
   reason_code      text NOT NULL,
   created_at       timestamptz NOT NULL,
   CONSTRAINT pk_monitoring_budget_versions PRIMARY KEY (tenant_id, version),
   CONSTRAINT fk_monitoring_budget_versions_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_monitoring_budget_versions_units CHECK (allowance_units >= 0 AND version >= 1),
   CONSTRAINT ck_monitoring_budget_versions_reason CHECK (reason_code ~ '^[a-z_]{1,40}$')
);
ALTER TABLE monitoring_budget_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE monitoring_budget_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON monitoring_budget_versions TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON monitoring_budget_versions TO app_rw;

CREATE TABLE monitoring_usage (
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   period       text NOT NULL,
   used         bigint NOT NULL DEFAULT 0,
   updated_at   timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_monitoring_usage PRIMARY KEY (tenant_id, period),
   CONSTRAINT fk_monitoring_usage_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_monitoring_usage_used CHECK (used >= 0),
   CONSTRAINT ck_monitoring_usage_period CHECK (period ~ '^[0-9]{4}-[0-9]{2}$')
);
ALTER TABLE monitoring_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE monitoring_usage FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON monitoring_usage TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON monitoring_usage TO app_rw;
GRANT UPDATE (used, updated_at) ON monitoring_usage TO app_rw;

CREATE TABLE throttle_events (
   tenant_id      uuid NOT NULL DEFAULT app_tenant(),
   period         text NOT NULL,
   step           text NOT NULL,
   used_percent   integer NOT NULL,
   at             timestamptz NOT NULL,
   CONSTRAINT pk_throttle_events PRIMARY KEY (tenant_id, period, step),
   CONSTRAINT fk_throttle_events_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_throttle_events_step CHECK (step IN ('alert_80', 'probe_only_lowest_scores',
      'lengthen_cadence', 'pause_non_priority', 'hard_stop')),
   CONSTRAINT ck_throttle_events_percent CHECK (used_percent BETWEEN 0 AND 100)
);
ALTER TABLE throttle_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE throttle_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON throttle_events TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON throttle_events TO app_rw;
