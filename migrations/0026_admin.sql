-- Migration 0026: operator plane (Phase 3 WS8; expand only). Spec docs/prompts/build_phase3.md
-- s5, AD1 to AD4, D-66, D-70. down_revision 0025.
--
-- Tenant-scoped (forced RLS):
--   support_grants: a tenant admin's time-boxed grant to one named 6th operator, with a reason
--     code and a scope (read: view-as only; write: a separate, second grant). Revocation is a
--     column set once; expiry is enforced by the server on every use.
--   support_session_events: insert-only, one row per support action (customer-visible).
--   quota_versions: insert-only per-tenant limit overrides (the newest version wins).
-- Platform (no tenant column, no tenant content):
--   feature_flags: insert-only flag versions (rule: tenants, plans, percentage).
--   flag_events: insert-only record of every flag change (actor, reason).
--   broker_keys: insert-only REFERENCES to platform-held broker keys (D-70); never the key.
--   operator_audit: insert-only record of operator-plane actions (identifiers only).
-- Definer-rights lookups (owner operator_resolver, NOLOGIN, column grants only):
--   support_grant_tenant(grant, operator): the tenant of one ACTIVE grant to that operator.
--   operator_tenant_overview(): tenant ids, creation time and counts; never names or content.
-- Rollback: 0026_admin.down.sql.

CREATE TABLE support_grants (
   id           uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   grantee      text NOT NULL,
   scope        text NOT NULL,
   reason_code  text NOT NULL,
   granted_by   text NOT NULL,
   granted_at   timestamptz NOT NULL,
   expires_at   timestamptz NOT NULL,
   revoked_at   timestamptz,
   revoked_by   text,
   CONSTRAINT pk_support_grants PRIMARY KEY (id),
   CONSTRAINT fk_support_grants_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_support_grants_scope CHECK (scope IN ('read', 'write')),
   CONSTRAINT ck_support_grants_reason CHECK (reason_code ~ '^[a-z_]{1,40}$'),
   CONSTRAINT ck_support_grants_grantee CHECK (grantee ~ '^[a-z0-9._@-]{1,120}$'),
   CONSTRAINT ck_support_grants_window CHECK (expires_at > granted_at),
   CONSTRAINT ck_support_grants_revoked CHECK ((revoked_at IS NULL) = (revoked_by IS NULL))
);
ALTER TABLE support_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE support_grants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON support_grants TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON support_grants TO app_rw;
GRANT UPDATE (revoked_at, revoked_by) ON support_grants TO app_rw;

CREATE TABLE support_session_events (
   id          bigserial NOT NULL,
   tenant_id   uuid NOT NULL DEFAULT app_tenant(),
   grant_id    uuid NOT NULL,
   operator    text NOT NULL,
   method      text NOT NULL,
   route       text NOT NULL,
   outcome     text NOT NULL,
   request_id  text,
   at          timestamptz NOT NULL,
   CONSTRAINT pk_support_session_events PRIMARY KEY (id),
   CONSTRAINT fk_support_session_events_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_support_session_events_method CHECK (method IN ('GET', 'POST', 'PUT',
      'PATCH', 'DELETE')),
   CONSTRAINT ck_support_session_events_outcome CHECK (outcome IN ('ok', 'denied')),
   CONSTRAINT ck_support_session_events_route CHECK (char_length(route) <= 200)
);
ALTER TABLE support_session_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE support_session_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON support_session_events TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON support_session_events TO app_rw;
GRANT USAGE ON SEQUENCE support_session_events_id_seq TO app_rw;

CREATE TABLE quota_versions (
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   dimension    text NOT NULL,
   version      integer NOT NULL,
   limit_value  bigint NOT NULL,
   set_by       text NOT NULL,
   reason_code  text NOT NULL,
   created_at   timestamptz NOT NULL,
   CONSTRAINT pk_quota_versions PRIMARY KEY (tenant_id, dimension, version),
   CONSTRAINT fk_quota_versions_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_quota_versions_dimension CHECK (dimension IN ('accounts', 'runs_per_period',
      'refreshes_per_period', 'seats', 'connectors', 'storage_mb')),
   CONSTRAINT ck_quota_versions_value CHECK (limit_value >= 0 AND version >= 1),
   CONSTRAINT ck_quota_versions_reason CHECK (reason_code ~ '^[a-z_]{1,40}$')
);
ALTER TABLE quota_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE quota_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON quota_versions TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON quota_versions TO app_rw;

CREATE TABLE feature_flags (
   flag        text NOT NULL,
   version     integer NOT NULL,
   rule        jsonb NOT NULL,
   created_by  text NOT NULL,
   created_at  timestamptz NOT NULL,
   CONSTRAINT pk_feature_flags PRIMARY KEY (flag, version),
   CONSTRAINT ck_feature_flags_name CHECK (flag ~ '^[a-z_]{1,60}$'),
   CONSTRAINT ck_feature_flags_rule CHECK (jsonb_typeof(rule) = 'object'
      AND octet_length(rule::text) <= 16384)
);
GRANT SELECT, INSERT ON feature_flags TO app_rw;

CREATE TABLE flag_events (
   id          bigserial NOT NULL,
   flag        text NOT NULL,
   version     integer NOT NULL,
   actor       text NOT NULL,
   reason_code text NOT NULL,
   at          timestamptz NOT NULL,
   CONSTRAINT pk_flag_events PRIMARY KEY (id),
   CONSTRAINT ck_flag_events_reason CHECK (reason_code ~ '^[a-z_]{1,40}$')
);
GRANT SELECT, INSERT ON flag_events TO app_rw;
GRANT USAGE ON SEQUENCE flag_events_id_seq TO app_rw;

CREATE TABLE broker_keys (
   provider_code  text NOT NULL,
   version        integer NOT NULL,
   secret_ref     text NOT NULL,
   created_by     text NOT NULL,
   reason_code    text NOT NULL,
   created_at     timestamptz NOT NULL,
   CONSTRAINT pk_broker_keys PRIMARY KEY (provider_code, version),
   CONSTRAINT ck_broker_keys_provider CHECK (provider_code ~ '^p_[a-z]+$'),
   CONSTRAINT ck_broker_keys_ref CHECK (secret_ref ~ '^[A-Za-z0-9/_.:-]+$'
      AND char_length(secret_ref) <= 300),
   CONSTRAINT ck_broker_keys_reason CHECK (reason_code ~ '^[a-z_]{1,40}$')
);
GRANT SELECT, INSERT ON broker_keys TO app_rw;

CREATE TABLE operator_audit (
   id          bigserial NOT NULL,
   operator    text NOT NULL,
   action      text NOT NULL,
   target      text,
   reason_code text,
   request_id  text,
   at          timestamptz NOT NULL,
   CONSTRAINT pk_operator_audit PRIMARY KEY (id),
   CONSTRAINT ck_operator_audit_action CHECK (action ~ '^[a-z_]+(\.[a-z_]+){1,2}$'),
   CONSTRAINT ck_operator_audit_target CHECK (target IS NULL OR char_length(target) <= 200)
);
GRANT SELECT, INSERT ON operator_audit TO app_rw;
GRANT USAGE ON SEQUENCE operator_audit_id_seq TO app_rw;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'operator_resolver') THEN
    CREATE ROLE operator_resolver NOLOGIN NOBYPASSRLS;
  END IF;
END $$;
CREATE POLICY operator_resolve ON support_grants FOR SELECT TO operator_resolver USING (true);
GRANT SELECT (id, tenant_id, grantee, scope, expires_at, revoked_at) ON support_grants
   TO operator_resolver;
CREATE POLICY operator_overview ON tenants FOR SELECT TO operator_resolver USING (true);
GRANT SELECT (id, created_at, plan) ON tenants TO operator_resolver;
CREATE POLICY operator_overview ON memberships FOR SELECT TO operator_resolver USING (true);
GRANT SELECT (tenant_id, status) ON memberships TO operator_resolver;
CREATE POLICY operator_overview ON accounts FOR SELECT TO operator_resolver USING (true);
GRANT SELECT (tenant_id) ON accounts TO operator_resolver;
CREATE POLICY operator_overview ON account_runs FOR SELECT TO operator_resolver USING (true);
GRANT SELECT (tenant_id, status) ON account_runs TO operator_resolver;
CREATE POLICY operator_overview ON tenant_events FOR SELECT TO operator_resolver USING (true);
GRANT SELECT (tenant_id, seq, event) ON tenant_events TO operator_resolver;

-- reviewed: SECURITY DEFINER (owner operator_resolver, NOLOGIN, column grants only). Returns the
-- tenant of one grant to exactly this operator that is unrevoked and unexpired at p_now, and
-- its scope; nothing else.
CREATE FUNCTION support_grant_tenant(p_grant uuid, p_operator text, p_now timestamptz)
   RETURNS TABLE (tenant_id uuid, scope text)
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT g.tenant_id, g.scope FROM support_grants g
    WHERE g.id = p_grant AND g.grantee = p_operator AND g.revoked_at IS NULL
      AND g.expires_at > p_now
$$;
-- reviewed: SECURITY DEFINER (owner operator_resolver). Metadata only: tenant id, creation time,
-- plan, suspension, and counts of active members, accounts and live runs. No name, no content.
CREATE FUNCTION operator_tenant_overview()
   RETURNS TABLE (tenant_id uuid, created_at timestamptz, plan text, suspended boolean,
                  members bigint, accounts bigint, live_runs bigint)
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT t.id, t.created_at, t.plan,
          COALESCE((SELECT e.event = 'suspended' FROM tenant_events e WHERE e.tenant_id = t.id
                    AND e.event IN ('suspended', 'reactivated') ORDER BY e.seq DESC LIMIT 1),
                   false),
          (SELECT count(*) FROM memberships m WHERE m.tenant_id = t.id AND m.status = 'active'),
          (SELECT count(*) FROM accounts a WHERE a.tenant_id = t.id),
          (SELECT count(*) FROM account_runs r WHERE r.tenant_id = t.id
             AND r.status NOT IN ('COMPLETED', 'PARTIAL', 'BUDGET_EXCEEDED', 'FAILED', 'CANCELED'))
     FROM tenants t ORDER BY t.created_at, t.id
$$;
ALTER FUNCTION support_grant_tenant(uuid, text, timestamptz) OWNER TO operator_resolver;
ALTER FUNCTION operator_tenant_overview() OWNER TO operator_resolver;
REVOKE ALL ON FUNCTION support_grant_tenant(uuid, text, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION operator_tenant_overview() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION support_grant_tenant(uuid, text, timestamptz) TO app_rw;
GRANT EXECUTE ON FUNCTION operator_tenant_overview() TO app_rw;

-- Tenant provisioning from the operator plane: a NOLOGIN owner that may only insert a tenant row
-- and its empty wallet.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tenant_provisioner') THEN
    CREATE ROLE tenant_provisioner NOLOGIN NOBYPASSRLS;
  END IF;
END $$;
CREATE POLICY tenant_provision ON tenants FOR INSERT TO tenant_provisioner WITH CHECK (true);
CREATE POLICY tenant_provision_read ON tenants FOR SELECT TO tenant_provisioner USING (true);
CREATE POLICY tenant_provision ON wallets FOR INSERT TO tenant_provisioner WITH CHECK (true);
GRANT INSERT (name) ON tenants TO tenant_provisioner;
GRANT SELECT (id) ON tenants TO tenant_provisioner;
GRANT INSERT (tenant_id, balance) ON wallets TO tenant_provisioner;
-- reviewed: SECURITY DEFINER (owner tenant_provisioner, NOLOGIN, insert-only grants). Creates one
-- tenant with a zero wallet and returns only its id; it can read and change nothing else.
CREATE FUNCTION provision_tenant(p_name text)
   RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
   tid uuid;
BEGIN
   IF p_name IS NULL OR char_length(p_name) NOT BETWEEN 1 AND 120 THEN
      RAISE EXCEPTION 'provision_tenant: name out of range' USING ERRCODE = 'check_violation';
   END IF;
   INSERT INTO tenants (name) VALUES (p_name) RETURNING id INTO tid;
   INSERT INTO wallets (tenant_id, balance) VALUES (tid, 0);
   RETURN tid;
END $$;
ALTER FUNCTION provision_tenant(text) OWNER TO tenant_provisioner;
REVOKE ALL ON FUNCTION provision_tenant(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION provision_tenant(text) TO app_rw;
