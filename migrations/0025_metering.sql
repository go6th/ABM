-- Migration 0025: metering (Phase 3 WS7; expand only). Spec docs/phase3/architecture.md,
-- MT1 to MT3, D-67. down_revision 0024.
--
-- usage_events: insert-only; one row per (kind, subject) by the unique idempotency key, so a
--   retried or replayed action adds nothing (run_settled is emitted once per run).
-- plan_limits: PLATFORM plan definitions (no tenant column, no tenant data): insert-only
--   versions; version 1 is seeded from config/plans.yaml (a test keeps them identical).
-- plan_assignments: insert-only versions of a tenant's plan.
-- usage_counters: DERIVED and mutable (documented): the per-period count each limit check
--   increments with ONE conditional update (used < limit), so concurrent requests can never pass
--   a limit. Recomputable from usage_events (the reconcile job compares them).
-- usage_thresholds: insert-only, unique per (period, dimension): the soft-threshold notice is
--   sent once.
-- Rollback: 0025_metering.down.sql.

CREATE TABLE usage_events (
   id               bigserial NOT NULL,
   tenant_id        uuid NOT NULL DEFAULT app_tenant(),
   kind             text NOT NULL,
   subject_id       text NOT NULL,
   idempotency_key  text NOT NULL,
   units            integer NOT NULL DEFAULT 1,
   period           text NOT NULL,
   at               timestamptz NOT NULL,
   CONSTRAINT pk_usage_events PRIMARY KEY (id),
   CONSTRAINT fk_usage_events_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT uq_usage_events_key UNIQUE (tenant_id, idempotency_key),
   CONSTRAINT ck_usage_events_kind CHECK (kind IN ('account_added', 'run_started', 'run_settled',
      'refresh_run', 'export_created')),
   CONSTRAINT ck_usage_events_units CHECK (units >= 0),
   CONSTRAINT ck_usage_events_period CHECK (period ~ '^[0-9]{4}-[0-9]{2}$'),
   CONSTRAINT ck_usage_events_subject CHECK (char_length(subject_id) BETWEEN 1 AND 200)
);
CREATE INDEX ix_usage_events_period ON usage_events (tenant_id, period, kind);
ALTER TABLE usage_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON usage_events TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON usage_events TO app_rw;
GRANT USAGE ON SEQUENCE usage_events_id_seq TO app_rw;

CREATE TABLE plan_limits (
   plan_code    text NOT NULL,
   version      integer NOT NULL,
   dimension    text NOT NULL,
   limit_value  bigint NOT NULL,
   created_by   text NOT NULL,
   created_at   timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_plan_limits PRIMARY KEY (plan_code, version, dimension),
   CONSTRAINT ck_plan_limits_code CHECK (plan_code ~ '^[a-z_]{1,40}$'),
   CONSTRAINT ck_plan_limits_dimension CHECK (dimension IN ('accounts', 'runs_per_period',
      'refreshes_per_period', 'seats', 'connectors', 'storage_mb')),
   CONSTRAINT ck_plan_limits_value CHECK (limit_value >= 0)
);
GRANT SELECT, INSERT ON plan_limits TO app_rw;
INSERT INTO plan_limits (plan_code, version, dimension, limit_value, created_by) VALUES
   ('pilot', 1, 'accounts', 10, 'migration_0025'),
   ('pilot', 1, 'runs_per_period', 20, 'migration_0025'),
   ('pilot', 1, 'refreshes_per_period', 0, 'migration_0025'),
   ('pilot', 1, 'seats', 10, 'migration_0025'),
   ('pilot', 1, 'connectors', 3, 'migration_0025'),
   ('pilot', 1, 'storage_mb', 1024, 'migration_0025'),
   ('standard', 1, 'accounts', 50, 'migration_0025'),
   ('standard', 1, 'runs_per_period', 100, 'migration_0025'),
   ('standard', 1, 'refreshes_per_period', 200, 'migration_0025'),
   ('standard', 1, 'seats', 25, 'migration_0025'),
   ('standard', 1, 'connectors', 5, 'migration_0025'),
   ('standard', 1, 'storage_mb', 10240, 'migration_0025');

CREATE TABLE plan_assignments (
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   version      integer NOT NULL,
   plan_code    text NOT NULL,
   assigned_by  text NOT NULL,
   reason_code  text NOT NULL,
   created_at   timestamptz NOT NULL,
   CONSTRAINT pk_plan_assignments PRIMARY KEY (tenant_id, version),
   CONSTRAINT fk_plan_assignments_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_plan_assignments_version CHECK (version >= 1),
   CONSTRAINT ck_plan_assignments_code CHECK (plan_code ~ '^[a-z_]{1,40}$'),
   CONSTRAINT ck_plan_assignments_reason CHECK (reason_code ~ '^[a-z_]{1,40}$')
);
ALTER TABLE plan_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE plan_assignments FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON plan_assignments TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON plan_assignments TO app_rw;

CREATE TABLE usage_counters (
   tenant_id   uuid NOT NULL DEFAULT app_tenant(),
   period      text NOT NULL,
   dimension   text NOT NULL,
   used        bigint NOT NULL DEFAULT 0,
   updated_at  timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_usage_counters PRIMARY KEY (tenant_id, period, dimension),
   CONSTRAINT fk_usage_counters_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_usage_counters_used CHECK (used >= 0),
   CONSTRAINT ck_usage_counters_period CHECK (period ~ '^([0-9]{4}-[0-9]{2}|all)$')
);
ALTER TABLE usage_counters ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_counters FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON usage_counters TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON usage_counters TO app_rw;
GRANT UPDATE (used, updated_at) ON usage_counters TO app_rw;

CREATE TABLE usage_thresholds (
   tenant_id   uuid NOT NULL DEFAULT app_tenant(),
   period      text NOT NULL,
   dimension   text NOT NULL,
   percent     integer NOT NULL,
   at          timestamptz NOT NULL,
   CONSTRAINT pk_usage_thresholds PRIMARY KEY (tenant_id, period, dimension),
   CONSTRAINT fk_usage_thresholds_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_usage_thresholds_percent CHECK (percent BETWEEN 1 AND 100)
);
ALTER TABLE usage_thresholds ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_thresholds FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON usage_thresholds TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON usage_thresholds TO app_rw;
