-- Migration 0041: ABM agent layer (expand only). Spec docs/abm/, D-96, D-97, D-102. down 0040.
--
-- account_runs gains the run mode (D-102: full | pull | build), the parent pull run of a build run
--   (same tenant, enforced by a composite foreign key) and the pinned skill config version (G17).
-- pull_manifests: insert-only, one per pull (or full) run: what Phase 1 stored per coverage row
--   (references, query metadata, cost), never an interpretation (G5, G15).
-- people_data_consents: insert-only tenant-level consent records (D-97); revocation is a new row.
-- abm_sense_checks: insert-only sense-check results bound to their inputs (G6).
-- Rollback: 0041_abm_agents.down.sql.

ALTER TABLE account_runs ADD CONSTRAINT uq_account_runs_tenant_id UNIQUE (tenant_id, id);
ALTER TABLE account_runs ADD COLUMN run_mode text NOT NULL DEFAULT 'full';
ALTER TABLE account_runs ADD COLUMN parent_run_id uuid;
ALTER TABLE account_runs ADD COLUMN skill_config_version text;
ALTER TABLE account_runs ADD COLUMN skill_config_reason text;
ALTER TABLE account_runs ADD CONSTRAINT ck_account_runs_run_mode
   CHECK (run_mode IN ('full', 'pull', 'build'));
ALTER TABLE account_runs ADD CONSTRAINT ck_account_runs_build_parent
   CHECK ((run_mode = 'build') = (parent_run_id IS NOT NULL));
ALTER TABLE account_runs ADD CONSTRAINT ck_account_runs_config_reason
   CHECK (skill_config_reason IS NULL OR skill_config_reason IN ('current', 'inherited', 'explicit_override'));
ALTER TABLE account_runs ADD CONSTRAINT fk_account_runs_parent
   FOREIGN KEY (tenant_id, parent_run_id) REFERENCES account_runs (tenant_id, id);

CREATE TABLE pull_manifests (
   tenant_id        uuid NOT NULL DEFAULT app_tenant(),
   run_id           uuid NOT NULL,
   account_key      text NOT NULL,
   config_version   text NOT NULL,
   digest           text NOT NULL,
   manifest         jsonb NOT NULL,
   created_at       timestamptz NOT NULL,
   CONSTRAINT pk_pull_manifests PRIMARY KEY (tenant_id, run_id),
   CONSTRAINT fk_pull_manifests_run FOREIGN KEY (tenant_id, run_id) REFERENCES account_runs (tenant_id, id),
   CONSTRAINT ck_pull_manifests_digest CHECK (digest ~ '^[0-9a-f]{64}$')
);
CREATE INDEX ix_pull_manifests_account ON pull_manifests (tenant_id, account_key, created_at DESC);
ALTER TABLE pull_manifests ENABLE ROW LEVEL SECURITY;
ALTER TABLE pull_manifests FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON pull_manifests TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON pull_manifests TO app_rw;

CREATE TABLE people_data_consents (
   tenant_id        uuid NOT NULL DEFAULT app_tenant(),
   id               bigserial NOT NULL,
   granted_by       text NOT NULL,
   granted_at       timestamptz NOT NULL,
   text_version     text NOT NULL,
   scopes           text[] NOT NULL,
   revoked_at       timestamptz,
   CONSTRAINT pk_people_data_consents PRIMARY KEY (tenant_id, id),
   CONSTRAINT fk_people_data_consents_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_people_data_consents_version CHECK (text_version ~ '^[A-Za-z0-9._-]{1,40}$')
);
ALTER TABLE people_data_consents ENABLE ROW LEVEL SECURITY;
ALTER TABLE people_data_consents FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON people_data_consents TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON people_data_consents TO app_rw;
GRANT USAGE ON SEQUENCE people_data_consents_id_seq TO app_rw;

CREATE TABLE abm_sense_checks (
   tenant_id        uuid NOT NULL DEFAULT app_tenant(),
   run_id           uuid NOT NULL,
   seq              integer NOT NULL,
   manifest_digest  text NOT NULL,
   facts_digest     text NOT NULL,
   findings         integer NOT NULL,
   proposals        integer NOT NULL,
   created_at       timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_abm_sense_checks PRIMARY KEY (tenant_id, run_id, seq),
   CONSTRAINT fk_abm_sense_checks_run FOREIGN KEY (tenant_id, run_id) REFERENCES account_runs (tenant_id, id),
   CONSTRAINT ck_abm_sense_checks_counts CHECK (findings >= 0 AND proposals >= 0 AND seq >= 1)
);
ALTER TABLE abm_sense_checks ENABLE ROW LEVEL SECURITY;
ALTER TABLE abm_sense_checks FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON abm_sense_checks TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON abm_sense_checks TO app_rw;
