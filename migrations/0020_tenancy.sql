-- Migration 0020: tenancy (Phase 3 WS1; expand only). Spec docs/prompts/build_phase3.md s5.
-- down_revision 0019.
--
-- users: one row per person per tenant (the subject is what memberships.user_id holds).
-- memberships: expand only: status (active, suspended, removed) and version for conditional
--   updates; the role check is WIDENED to admit 'analyst' (D-61 proposal). Widening a CHECK admits
--   more values and changes no existing column or row; the old app never writes 'analyst'.
-- membership_events, tenant_settings_versions, tenant_events: insert-only (no UPDATE or DELETE
--   grant). A tenant's status is its latest lifecycle event (no mutable status column); the per-
--   tenant seq is unique, so two concurrent lifecycle changes cannot both apply (one insert wins).
-- account_runs.settings_version: the settings version a run used (TN5; nullable, expand).
-- tenant_eraser: NOLOGIN, NOBYPASSRLS, a member of app_rw (its policies apply unchanged).
--   erase_rows() is the ONLY delete path of the deletion
--   cascade (SaaS 3.1 rule 8): it deletes a bounded batch of the CONTEXT tenant's rows from a
--   hard-coded allow-list of derived tables, and only while an approved, unfinished deletion
--   request exists. Ledgers, audit, usage, lifecycle and settings rows are never in the list.
-- Rollback: 0020_tenancy.down.sql.

CREATE TABLE users (
   id            uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id     uuid NOT NULL DEFAULT app_tenant(),
   subject       text NOT NULL,
   email         text,
   display_name  text,
   created_at    timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_users PRIMARY KEY (id),
   CONSTRAINT fk_users_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT uq_users_subject UNIQUE (tenant_id, subject),
   CONSTRAINT ck_users_subject CHECK (char_length(subject) BETWEEN 1 AND 255),
   CONSTRAINT ck_users_email CHECK (email IS NULL OR (email = lower(email)
      AND char_length(email) <= 320 AND email ~ '^[^@[:space:]]+@[^@[:space:]]+$')),
   CONSTRAINT ck_users_display_name CHECK (display_name IS NULL
      OR char_length(display_name) <= 200)
);
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON users TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON users TO app_rw;
GRANT UPDATE (display_name) ON users TO app_rw;

ALTER TABLE memberships ADD COLUMN status text NOT NULL DEFAULT 'active';
ALTER TABLE memberships ADD COLUMN version integer NOT NULL DEFAULT 1;
ALTER TABLE memberships ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE memberships ADD CONSTRAINT ck_memberships_status
   CHECK (status IN ('active', 'suspended', 'removed'));
ALTER TABLE memberships ADD CONSTRAINT ck_memberships_version CHECK (version >= 1);
ALTER TABLE memberships DROP CONSTRAINT memberships_role_check;
ALTER TABLE memberships ADD CONSTRAINT memberships_role_check
   CHECK (role IN ('admin', 'analyst', 'seller', 'viewer'));

CREATE TABLE membership_events (
   id          bigserial NOT NULL,
   tenant_id   uuid NOT NULL DEFAULT app_tenant(),
   user_id     text NOT NULL,
   event       text NOT NULL,
   role        text,
   prior_role  text,
   actor       text NOT NULL,
   reason      text,
   at          timestamptz NOT NULL,
   CONSTRAINT pk_membership_events PRIMARY KEY (id),
   CONSTRAINT fk_membership_events_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_membership_events_event CHECK (event IN ('added', 'provisioned', 'role_changed',
      'suspended', 'reactivated', 'removed')),
   CONSTRAINT ck_membership_events_roles CHECK (
      (role IS NULL OR role IN ('admin', 'analyst', 'seller', 'viewer'))
      AND (prior_role IS NULL OR prior_role IN ('admin', 'analyst', 'seller', 'viewer'))),
   CONSTRAINT ck_membership_events_reason CHECK (reason IS NULL OR char_length(reason) <= 300)
);
CREATE INDEX ix_membership_events_user ON membership_events (tenant_id, user_id, id);
ALTER TABLE membership_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE membership_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON membership_events TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON membership_events TO app_rw;
GRANT USAGE ON SEQUENCE membership_events_id_seq TO app_rw;

CREATE TABLE tenant_settings_versions (
   tenant_id      uuid NOT NULL DEFAULT app_tenant(),
   version        integer NOT NULL,
   settings_json  jsonb NOT NULL,
   settings_hash  text NOT NULL,
   created_by     text NOT NULL,
   created_at     timestamptz NOT NULL,
   CONSTRAINT pk_tenant_settings_versions PRIMARY KEY (tenant_id, version),
   CONSTRAINT fk_tenant_settings_versions_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_tenant_settings_versions_version CHECK (version >= 1),
   CONSTRAINT ck_tenant_settings_versions_hash CHECK (settings_hash ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_tenant_settings_versions_json CHECK (jsonb_typeof(settings_json) = 'object'
      AND octet_length(settings_json::text) <= 65536)
);
ALTER TABLE tenant_settings_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_settings_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenant_settings_versions TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON tenant_settings_versions TO app_rw;

ALTER TABLE account_runs ADD COLUMN settings_version integer;
ALTER TABLE account_runs ADD CONSTRAINT fk_account_runs_settings_version
   FOREIGN KEY (tenant_id, settings_version)
   REFERENCES tenant_settings_versions (tenant_id, version);

CREATE TABLE tenant_events (
   id           bigserial NOT NULL,
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   seq          integer NOT NULL,
   event        text NOT NULL,
   actor        text NOT NULL,
   actor_plane  text NOT NULL,
   reason_code  text,
   detail_json  jsonb NOT NULL DEFAULT '{}'::jsonb,
   at           timestamptz NOT NULL,
   CONSTRAINT pk_tenant_events PRIMARY KEY (id),
   CONSTRAINT fk_tenant_events_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT uq_tenant_events_seq UNIQUE (tenant_id, seq),
   CONSTRAINT ck_tenant_events_seq CHECK (seq >= 1),
   CONSTRAINT ck_tenant_events_event CHECK (event IN ('created', 'suspended', 'reactivated',
      'deletion_requested', 'deletion_dry_run', 'deletion_approved', 'deletion_batch',
      'deletion_blocked', 'deletion_completed', 'deletion_cancelled')),
   CONSTRAINT ck_tenant_events_plane CHECK (actor_plane IN ('tenant', 'operator', 'system')),
   CONSTRAINT ck_tenant_events_reason CHECK (reason_code IS NULL OR reason_code ~ '^[a-z_]{1,40}$'),
   CONSTRAINT ck_tenant_events_detail CHECK (jsonb_typeof(detail_json) = 'object'
      AND octet_length(detail_json::text) <= 16384)
);
ALTER TABLE tenant_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenant_events TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON tenant_events TO app_rw;
GRANT USAGE ON SEQUENCE tenant_events_id_seq TO app_rw;

-- The deletion cascade's eraser (see the header). Roles are cluster-global: create once.
-- tenant_eraser is a MEMBER of app_rw, so app_rw's tenant policies (TO app_rw, USING and WITH
-- CHECK on the context tenant) apply to it unchanged; no new policy is added to any table. It is
-- granted DELETE on the erasable tables only; app_rw itself gains nothing.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tenant_eraser') THEN
    CREATE ROLE tenant_eraser NOLOGIN NOBYPASSRLS;
  END IF;
END $$;
GRANT app_rw TO tenant_eraser;
GRANT DELETE ON fact_evidence, grade_events, facts, claim_drops, report_sections, deliverables,
   job_posting_platform_sentences, job_posting_classifications, job_posting_observations,
   job_postings, tech_detections, chunks, verifier_findings TO tenant_eraser;

-- reviewed: SECURITY DEFINER (owner tenant_eraser, NOLOGIN, NOBYPASSRLS). Deletes at most
-- p_limit rows of the CALLER's context tenant (app_rw's tenant policies, inherited) from one table
-- of the hard-coded allow-list, only while deletion request p_request has a dry run, was approved
-- by a person other than its requester, and is neither completed nor cancelled. Returns the
-- number of rows deleted.
CREATE FUNCTION erase_rows(p_request uuid, p_table text, p_limit integer)
   RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
   n integer;
BEGIN
   IF app_tenant() IS NULL THEN
      RAISE EXCEPTION 'erase_rows: no tenant context' USING ERRCODE = 'insufficient_privilege';
   END IF;
   IF p_limit IS NULL OR p_limit < 1 OR p_limit > 5000 THEN
      RAISE EXCEPTION 'erase_rows: batch size out of range' USING ERRCODE = 'check_violation';
   END IF;
   IF p_table NOT IN ('fact_evidence', 'grade_events', 'facts', 'claim_drops', 'report_sections',
         'deliverables', 'job_posting_platform_sentences', 'job_posting_classifications',
         'job_posting_observations', 'job_postings', 'tech_detections', 'chunks',
         'verifier_findings') THEN
      RAISE EXCEPTION 'erase_rows: % is not erasable', p_table
         USING ERRCODE = 'insufficient_privilege';
   END IF;
   IF NOT EXISTS (SELECT 1 FROM tenant_events d WHERE d.event = 'deletion_dry_run'
                   AND d.detail_json ->> 'request_id' = p_request::text)
      OR NOT EXISTS (SELECT 1 FROM tenant_events a JOIN tenant_events r
                   ON r.event = 'deletion_requested'
                  AND r.detail_json ->> 'request_id' = p_request::text
                WHERE a.event = 'deletion_approved'
                  AND a.detail_json ->> 'request_id' = p_request::text
                  AND a.actor <> r.actor)
      OR EXISTS (SELECT 1 FROM tenant_events e
                  WHERE e.event IN ('deletion_completed', 'deletion_cancelled')
                    AND e.detail_json ->> 'request_id' = p_request::text) THEN
      RAISE EXCEPTION 'erase_rows: request % is not approved and open', p_request
         USING ERRCODE = 'insufficient_privilege';
   END IF;
   EXECUTE format('DELETE FROM %I WHERE ctid IN (SELECT ctid FROM %I LIMIT %s)',
                  p_table, p_table, p_limit);
   GET DIAGNOSTICS n = ROW_COUNT;
   RETURN n;
END $$;
ALTER FUNCTION erase_rows(uuid, text, integer) OWNER TO tenant_eraser;
REVOKE ALL ON FUNCTION erase_rows(uuid, text, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION erase_rows(uuid, text, integer) TO app_rw;
