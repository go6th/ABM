-- Migration 0023: views and exports (Phase 3 WS4; expand only). Spec docs/phase3/architecture.md
-- s5, VW1 to VW6. down_revision 0022.
--
-- view_rows: DERIVED and recomputable: the five client tables of one finished run, written once
--   from the same mapping the export builders render (exports.builder.map_snapshot), so the API
--   queries the very rows the files contain. Insert-only. search_doc is a generated tsvector so
--   full-text search runs in SQL inside the tenant's transaction (VW3), never after retrieval.
-- view_materializations: one row per materialised run (unique), with the per-table counts and
--   the rows hash the parity check binds to.
-- saved_views: a tenant object holding a VALIDATED query spec (never SQL); edits are conditional
--   on version; archiving is a column (no delete).
-- export_packages: every export a tenant asked for, with its parity and gate outcome; a package
--   is downloadable only when delivered (status = 'delivered').
-- branding_assets: validated, re-encoded logos (content hash; object key under the tenant prefix).
-- Rollback: 0023_views.down.sql.

CREATE TABLE view_rows (
   id           bigserial NOT NULL,
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   run_id       uuid NOT NULL,
   account_id   uuid NOT NULL,
   table_kind   text NOT NULL,
   position     integer NOT NULL,
   ref          text NOT NULL,
   row_id       text NOT NULL,
   cells        jsonb NOT NULL,
   search_text  text NOT NULL DEFAULT '',
   search_doc   tsvector GENERATED ALWAYS AS (to_tsvector('simple', search_text)) STORED,
   source_code  text,
   source_url   text,
   source_state text NOT NULL,
   CONSTRAINT pk_view_rows PRIMARY KEY (id),
   CONSTRAINT fk_view_rows_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT uq_view_rows_position UNIQUE (tenant_id, run_id, table_kind, position),
   CONSTRAINT ck_view_rows_kind CHECK (table_kind IN ('people', 'jobs', 'tech', 'facts',
      'coverage')),
   CONSTRAINT ck_view_rows_position CHECK (position >= 1),
   CONSTRAINT ck_view_rows_cells CHECK (jsonb_typeof(cells) = 'object'
      AND octet_length(cells::text) <= 262144),
   CONSTRAINT ck_view_rows_source CHECK (source_state IN ('linked', 'no_source')
      AND ((source_state = 'linked') = (source_code IS NOT NULL AND source_url IS NOT NULL))),
   CONSTRAINT ck_view_rows_url CHECK (source_url IS NULL OR source_url ~ '^https?://')
);
CREATE INDEX ix_view_rows_table ON view_rows (tenant_id, run_id, table_kind, position);
CREATE INDEX ix_view_rows_search ON view_rows USING gin (search_doc);
ALTER TABLE view_rows ENABLE ROW LEVEL SECURITY;
ALTER TABLE view_rows FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON view_rows TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON view_rows TO app_rw;
GRANT USAGE ON SEQUENCE view_rows_id_seq TO app_rw;

CREATE TABLE view_materializations (
   tenant_id         uuid NOT NULL DEFAULT app_tenant(),
   run_id            uuid NOT NULL,
   account_id        uuid NOT NULL,
   counts            jsonb NOT NULL,
   rows_hash         text NOT NULL,
   settings_version  integer,
   status            text NOT NULL,
   reason            text,
   created_at        timestamptz NOT NULL,
   CONSTRAINT pk_view_materializations PRIMARY KEY (tenant_id, run_id),
   CONSTRAINT fk_view_materializations_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_view_materializations_hash CHECK (rows_hash ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_view_materializations_status CHECK (status IN ('ready', 'withheld')
      AND ((status = 'withheld') = (reason IS NOT NULL))),
   CONSTRAINT ck_view_materializations_reason CHECK (reason IS NULL OR reason ~ '^[a-z_]{1,40}$')
);
ALTER TABLE view_materializations ENABLE ROW LEVEL SECURITY;
ALTER TABLE view_materializations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON view_materializations TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON view_materializations TO app_rw;

CREATE TABLE saved_views (
   id           uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   owner        text NOT NULL,
   table_kind   text NOT NULL,
   name         text NOT NULL,
   spec         jsonb NOT NULL,
   shared       boolean NOT NULL DEFAULT false,
   version      integer NOT NULL DEFAULT 1,
   archived_at  timestamptz,
   created_at   timestamptz NOT NULL,
   updated_at   timestamptz NOT NULL,
   CONSTRAINT pk_saved_views PRIMARY KEY (id),
   CONSTRAINT fk_saved_views_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_saved_views_kind CHECK (table_kind IN ('people', 'jobs', 'tech', 'facts',
      'coverage')),
   CONSTRAINT ck_saved_views_name CHECK (char_length(name) BETWEEN 1 AND 80),
   CONSTRAINT ck_saved_views_spec CHECK (jsonb_typeof(spec) = 'object'
      AND octet_length(spec::text) <= 8192),
   CONSTRAINT ck_saved_views_version CHECK (version >= 1)
);
CREATE INDEX ix_saved_views_owner ON saved_views (tenant_id, owner);
ALTER TABLE saved_views ENABLE ROW LEVEL SECURITY;
ALTER TABLE saved_views FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON saved_views TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON saved_views TO app_rw;
GRANT UPDATE (name, spec, shared, version, archived_at, updated_at) ON saved_views TO app_rw;

CREATE TABLE export_packages (
   id             uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id      uuid NOT NULL DEFAULT app_tenant(),
   run_id         uuid NOT NULL,
   account_id     uuid NOT NULL,
   kind           text NOT NULL,
   status         text NOT NULL,
   reason         text,
   files          jsonb NOT NULL DEFAULT '[]'::jsonb,
   parity         jsonb NOT NULL DEFAULT '{}'::jsonb,
   rows_hash      text,
   requested_by   text NOT NULL,
   created_at     timestamptz NOT NULL,
   CONSTRAINT pk_export_packages PRIMARY KEY (id),
   CONSTRAINT fk_export_packages_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_export_packages_kind CHECK (kind IN ('files', 'csv', 'pdf')),
   CONSTRAINT ck_export_packages_status CHECK (status IN ('delivered', 'blocked')
      AND ((status = 'blocked') = (reason IS NOT NULL))),
   CONSTRAINT ck_export_packages_reason CHECK (reason IS NULL OR reason ~ '^[a-z_]{1,40}$'),
   CONSTRAINT ck_export_packages_json CHECK (jsonb_typeof(files) = 'array'
      AND jsonb_typeof(parity) = 'object' AND octet_length(files::text) <= 65536
      AND octet_length(parity::text) <= 65536)
);
ALTER TABLE export_packages ENABLE ROW LEVEL SECURITY;
ALTER TABLE export_packages FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON export_packages TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON export_packages TO app_rw;

CREATE TABLE branding_assets (
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   sha256       text NOT NULL,
   object_key   text NOT NULL,
   media_type   text NOT NULL,
   width        integer NOT NULL,
   height       integer NOT NULL,
   bytes        integer NOT NULL,
   uploaded_by  text NOT NULL,
   created_at   timestamptz NOT NULL,
   CONSTRAINT pk_branding_assets PRIMARY KEY (tenant_id, sha256),
   CONSTRAINT fk_branding_assets_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_branding_assets_sha CHECK (sha256 ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_branding_assets_type CHECK (media_type = 'image/png'),
   CONSTRAINT ck_branding_assets_key CHECK (object_key ~ '^tenants/[0-9a-f-]{36}/branding/'),
   CONSTRAINT ck_branding_assets_size CHECK (width > 0 AND height > 0 AND bytes > 0)
);
ALTER TABLE branding_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE branding_assets FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON branding_assets TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON branding_assets TO app_rw;
