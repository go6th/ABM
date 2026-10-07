-- Migration 0021: identity (Phase 3 WS2; expand only). Spec docs/prompts/build_phase3.md s5.
-- down_revision 0020.
--
-- idp_connections: one tenant's own IdP connection (OIDC or SAML). The login URL names the
--   connection's slug, never a tenant; the tenant is the connection's owner (ID1). Secrets are
--   Secret Manager REFERENCES only. Changes are conditional updates on `version`.
-- login_states: pending sign-ins (state, nonce, PKCE verifier, SAML request id), single use,
--   bound to a browser cookie hash, short-lived.
-- auth_sessions: server-side sessions; the cookie carries a random token and only its SHA-256 is
--   stored. The runtime role may update only last_seen_at, revoked_at and revoke_reason.
-- auth_events: insert-only sign-in, rotation, revocation and step-up events (no token, ever).
-- saml_assertion_ids: the assertion-id replay cache (unique per tenant and connection).
-- Three narrow definer-rights lookups resolve a tenant BEFORE a tenant context exists; each
--   returns identifiers only and takes a hash or slug that the caller cannot enumerate.
-- Rollback: 0021_identity.down.sql.

CREATE TABLE idp_connections (
   id                    uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id             uuid NOT NULL DEFAULT app_tenant(),
   slug                  text NOT NULL,
   protocol              text NOT NULL,
   issuer                text NOT NULL,
   client_id             text,
   client_secret_ref     text,
   authorization_endpoint text,
   token_endpoint        text,
   jwks_uri              text,
   saml_sso_url          text,
   saml_idp_cert_pem     text,
   sp_entity_id          text,
   verified_domains      text[] NOT NULL DEFAULT '{}',
   group_role_map        jsonb NOT NULL DEFAULT '{}'::jsonb,
   jit_enabled           boolean NOT NULL DEFAULT false,
   require_admin_mfa     boolean NOT NULL DEFAULT true,
   status                text NOT NULL DEFAULT 'active',
   version               integer NOT NULL DEFAULT 1,
   created_at            timestamptz NOT NULL DEFAULT now(),
   updated_at            timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_idp_connections PRIMARY KEY (id),
   CONSTRAINT fk_idp_connections_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT uq_idp_connections_slug UNIQUE (slug),
   CONSTRAINT uq_idp_connections_tenant_id UNIQUE (tenant_id, id),
   CONSTRAINT ck_idp_connections_slug CHECK (slug ~ '^[a-z0-9][a-z0-9-]{2,62}$'),
   CONSTRAINT ck_idp_connections_protocol CHECK (protocol IN ('oidc', 'saml')),
   CONSTRAINT ck_idp_connections_status CHECK (status IN ('active', 'disabled')),
   CONSTRAINT ck_idp_connections_version CHECK (version >= 1),
   CONSTRAINT ck_idp_connections_https CHECK (
      (authorization_endpoint IS NULL OR authorization_endpoint ~ '^https://')
      AND (token_endpoint IS NULL OR token_endpoint ~ '^https://')
      AND (jwks_uri IS NULL OR jwks_uri ~ '^https://')
      AND (saml_sso_url IS NULL OR saml_sso_url ~ '^https://')),
   CONSTRAINT ck_idp_connections_oidc CHECK (protocol <> 'oidc' OR (client_id IS NOT NULL
      AND authorization_endpoint IS NOT NULL AND token_endpoint IS NOT NULL
      AND jwks_uri IS NOT NULL)),
   CONSTRAINT ck_idp_connections_saml CHECK (protocol <> 'saml' OR (saml_sso_url IS NOT NULL
      AND saml_idp_cert_pem IS NOT NULL AND sp_entity_id IS NOT NULL)),
   CONSTRAINT ck_idp_connections_secret_ref CHECK (client_secret_ref IS NULL
      OR client_secret_ref ~ '^[A-Za-z0-9/_.:-]{1,300}$'),
   CONSTRAINT ck_idp_connections_map CHECK (jsonb_typeof(group_role_map) = 'object'
      AND octet_length(group_role_map::text) <= 16384)
);
ALTER TABLE idp_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE idp_connections FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON idp_connections TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE ON idp_connections TO app_rw;

CREATE TABLE login_states (
   id              uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id       uuid NOT NULL DEFAULT app_tenant(),
   connection_id   uuid NOT NULL,
   state_hash      text NOT NULL,
   browser_hash    text NOT NULL,
   nonce           text,
   code_verifier   text,
   saml_request_id text,
   redirect_uri    text NOT NULL,
   return_to       text NOT NULL DEFAULT '/',
   created_at      timestamptz NOT NULL,
   expires_at      timestamptz NOT NULL,
   consumed_at     timestamptz,
   CONSTRAINT pk_login_states PRIMARY KEY (id),
   CONSTRAINT fk_login_states_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT fk_login_states_connection FOREIGN KEY (tenant_id, connection_id)
      REFERENCES idp_connections (tenant_id, id),
   CONSTRAINT uq_login_states_state UNIQUE (state_hash),
   CONSTRAINT ck_login_states_hashes CHECK (state_hash ~ '^[0-9a-f]{64}$'
      AND browser_hash ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_login_states_return CHECK (return_to ~ '^/[A-Za-z0-9/_?=&.-]{0,200}$'
      AND return_to !~ '^//'),
   CONSTRAINT ck_login_states_window CHECK (expires_at > created_at)
);
ALTER TABLE login_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE login_states FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON login_states TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON login_states TO app_rw;
GRANT UPDATE (consumed_at) ON login_states TO app_rw;

CREATE TABLE auth_sessions (
   id                   uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id            uuid NOT NULL DEFAULT app_tenant(),
   user_id              text NOT NULL,
   token_hash           text NOT NULL,
   connection_id        uuid,
   amr                  text[] NOT NULL DEFAULT '{}',
   created_at           timestamptz NOT NULL,
   last_seen_at         timestamptz NOT NULL,
   absolute_expires_at  timestamptz NOT NULL,
   idle_timeout_s       integer NOT NULL,
   step_up_at           timestamptz,
   rotated_from         uuid,
   revoked_at           timestamptz,
   revoke_reason        text,
   CONSTRAINT pk_auth_sessions PRIMARY KEY (id),
   CONSTRAINT fk_auth_sessions_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT uq_auth_sessions_token UNIQUE (token_hash),
   CONSTRAINT ck_auth_sessions_token CHECK (token_hash ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_auth_sessions_window CHECK (absolute_expires_at > created_at
      AND idle_timeout_s > 0),
   CONSTRAINT ck_auth_sessions_revoked CHECK ((revoked_at IS NULL) = (revoke_reason IS NULL)),
   CONSTRAINT ck_auth_sessions_reason CHECK (revoke_reason IS NULL
      OR revoke_reason ~ '^[a-z_]{1,40}$')
);
CREATE INDEX ix_auth_sessions_user ON auth_sessions (tenant_id, user_id) WHERE revoked_at IS NULL;
ALTER TABLE auth_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth_sessions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON auth_sessions TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON auth_sessions TO app_rw;
GRANT UPDATE (last_seen_at, revoked_at, revoke_reason) ON auth_sessions TO app_rw;

CREATE TABLE auth_events (
   id             bigserial NOT NULL,
   tenant_id      uuid NOT NULL DEFAULT app_tenant(),
   user_id        text,
   session_id     uuid,
   connection_id  uuid,
   event          text NOT NULL,
   reason_code    text,
   request_id     text,
   at             timestamptz NOT NULL,
   CONSTRAINT pk_auth_events PRIMARY KEY (id),
   CONSTRAINT fk_auth_events_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_auth_events_event CHECK (event IN ('login_started', 'login_succeeded',
      'login_failed', 'session_rotated', 'session_revoked', 'revoked_all', 'step_up',
      'provisioned', 'session_expired', 'csrf_refused')),
   CONSTRAINT ck_auth_events_reason CHECK (reason_code IS NULL OR reason_code ~ '^[a-z_]{1,40}$')
);
ALTER TABLE auth_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON auth_events TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON auth_events TO app_rw;
GRANT USAGE ON SEQUENCE auth_events_id_seq TO app_rw;

CREATE TABLE saml_assertion_ids (
   tenant_id        uuid NOT NULL DEFAULT app_tenant(),
   connection_id    uuid NOT NULL,
   assertion_id     text NOT NULL,
   not_on_or_after  timestamptz NOT NULL,
   seen_at          timestamptz NOT NULL,
   CONSTRAINT pk_saml_assertion_ids PRIMARY KEY (tenant_id, connection_id, assertion_id),
   CONSTRAINT fk_saml_assertion_ids_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_saml_assertion_ids_id CHECK (char_length(assertion_id) BETWEEN 1 AND 256)
);
ALTER TABLE saml_assertion_ids ENABLE ROW LEVEL SECURITY;
ALTER TABLE saml_assertion_ids FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON saml_assertion_ids TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON saml_assertion_ids TO app_rw;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'identity_resolver') THEN
    CREATE ROLE identity_resolver NOLOGIN NOBYPASSRLS;
  END IF;
END $$;
-- identity_resolver reads the three lookup columns across tenants (no tenant context exists yet).
CREATE POLICY identity_resolve ON idp_connections FOR SELECT TO identity_resolver USING (true);
CREATE POLICY identity_resolve ON login_states FOR SELECT TO identity_resolver USING (true);
CREATE POLICY identity_resolve ON auth_sessions FOR SELECT TO identity_resolver USING (true);
GRANT SELECT (id, tenant_id, slug, status) ON idp_connections TO identity_resolver;
GRANT SELECT (tenant_id, state_hash) ON login_states TO identity_resolver;
GRANT SELECT (tenant_id, token_hash) ON auth_sessions TO identity_resolver;

-- reviewed: SECURITY DEFINER (owner identity_resolver, NOLOGIN, NOBYPASSRLS, column grants
-- only). Returns the owning tenant and id of one ACTIVE connection by its slug; nothing else.
CREATE FUNCTION idp_connection_tenant(p_slug text)
   RETURNS TABLE (tenant_id uuid, connection_id uuid)
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT c.tenant_id, c.id FROM idp_connections c
    WHERE c.slug = p_slug AND c.status = 'active'
$$;
-- reviewed: SECURITY DEFINER (owner identity_resolver). Returns only the tenant of the pending
-- sign-in whose state hash is p_state_hash (a 256-bit random value the browser holds).
CREATE FUNCTION login_state_tenant(p_state_hash text)
   RETURNS uuid
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT s.tenant_id FROM login_states s WHERE s.state_hash = p_state_hash
$$;
-- reviewed: SECURITY DEFINER (owner identity_resolver). Returns only the tenant of the session
-- whose token hash is p_token_hash (a 256-bit random token the cookie holds).
CREATE FUNCTION session_tenant(p_token_hash text)
   RETURNS uuid
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT s.tenant_id FROM auth_sessions s WHERE s.token_hash = p_token_hash
$$;
ALTER FUNCTION idp_connection_tenant(text) OWNER TO identity_resolver;
ALTER FUNCTION login_state_tenant(text) OWNER TO identity_resolver;
ALTER FUNCTION session_tenant(text) OWNER TO identity_resolver;
REVOKE ALL ON FUNCTION idp_connection_tenant(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION login_state_tenant(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION session_tenant(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION idp_connection_tenant(text) TO app_rw;
GRANT EXECUTE ON FUNCTION login_state_tenant(text) TO app_rw;
GRANT EXECUTE ON FUNCTION session_tenant(text) TO app_rw;
