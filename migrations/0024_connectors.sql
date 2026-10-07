-- Migration 0024: client history connectors (Phase 3 WS6; expand only). Spec
-- docs/prompts/build_phase3.md s5, CN1 to CN4, D-65. down_revision 0023.
--
-- connector_connections: one tenant source (Drive, Salesforce, HubSpot; Gmail and call notes are
--   flagged off). Tokens are Secret Manager REFERENCES; status changes are conditional updates on
--   version. Scopes are the ones granted, checked against the connector's scope table.
-- connector_oauth_states: pending consents, single use, bound to tenant, user and connection,
--   carrying the PKCE verifier; short-lived.
-- connector_grants: what an admin allowed (Drive folders, CRM objects and fields). Revocation is
--   a column set once, never a delete.
-- connector_quarantine: items that surfaced OUTSIDE the allow-list (or with unknown ancestry):
--   held off limits, never read, until a tenant admin clears them (status set once).
-- connector_items: what a fetch stored: object key under the tenant's connector-quarantine
--   prefix and content hash; the content stays there until the Phase B pipeline takes it.
-- connector_access_log: insert-only, one row per access (principal, connection, resource,
--   purpose, outcome); never a token or content.
-- Rollback: 0024_connectors.down.sql.

CREATE TABLE connector_connections (
   id              uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id       uuid NOT NULL DEFAULT app_tenant(),
   kind            text NOT NULL,
   status          text NOT NULL,
   scopes          text[] NOT NULL DEFAULT '{}',
   token_ref       text,
   consented_by    text,
   consented_at    timestamptz,
   cursor          text,
   last_fetch_at   timestamptz,
   broken_reason   text,
   version         integer NOT NULL DEFAULT 1,
   created_by      text NOT NULL,
   created_at      timestamptz NOT NULL,
   CONSTRAINT pk_connector_connections PRIMARY KEY (id),
   CONSTRAINT fk_connector_connections_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT uq_connector_connections_tenant_id UNIQUE (tenant_id, id),
   CONSTRAINT ck_connector_connections_kind CHECK (kind IN ('google_drive', 'salesforce',
      'hubspot', 'gmail', 'call_notes')),
   CONSTRAINT ck_connector_connections_status CHECK (status IN ('pending', 'active', 'broken',
      'disconnected')),
   CONSTRAINT ck_connector_connections_ref CHECK (token_ref IS NULL
      OR (token_ref ~ '^[A-Za-z0-9/_.:-]+$' AND char_length(token_ref) <= 300)),
   CONSTRAINT ck_connector_connections_broken CHECK ((status = 'broken') = (broken_reason IS NOT NULL)),
   CONSTRAINT ck_connector_connections_reason CHECK (broken_reason IS NULL
      OR broken_reason ~ '^[a-z_]{1,40}$'),
   CONSTRAINT ck_connector_connections_version CHECK (version >= 1)
);
ALTER TABLE connector_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE connector_connections FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON connector_connections TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON connector_connections TO app_rw;
GRANT UPDATE (status, scopes, token_ref, consented_by, consented_at, cursor, last_fetch_at,
   broken_reason, version) ON connector_connections TO app_rw;

CREATE TABLE connector_oauth_states (
   tenant_id      uuid NOT NULL DEFAULT app_tenant(),
   state_hash     text NOT NULL,
   connection_id  uuid NOT NULL,
   user_id        text NOT NULL,
   code_verifier  text NOT NULL,
   created_at     timestamptz NOT NULL,
   expires_at     timestamptz NOT NULL,
   consumed_at    timestamptz,
   CONSTRAINT pk_connector_oauth_states PRIMARY KEY (tenant_id, state_hash),
   CONSTRAINT fk_connector_oauth_states_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT fk_connector_oauth_states_connection FOREIGN KEY (tenant_id, connection_id)
      REFERENCES connector_connections (tenant_id, id),
   CONSTRAINT ck_connector_oauth_states_hash CHECK (state_hash ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_connector_oauth_states_window CHECK (expires_at > created_at)
);
ALTER TABLE connector_oauth_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE connector_oauth_states FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON connector_oauth_states TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON connector_oauth_states TO app_rw;
GRANT UPDATE (consumed_at) ON connector_oauth_states TO app_rw;

CREATE TABLE connector_grants (
   id             uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id      uuid NOT NULL DEFAULT app_tenant(),
   connection_id  uuid NOT NULL,
   grant_kind     text NOT NULL,
   resource_id    text NOT NULL,
   granted_by     text NOT NULL,
   granted_at     timestamptz NOT NULL,
   revoked_at     timestamptz,
   CONSTRAINT pk_connector_grants PRIMARY KEY (id),
   CONSTRAINT fk_connector_grants_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT fk_connector_grants_connection FOREIGN KEY (tenant_id, connection_id)
      REFERENCES connector_connections (tenant_id, id),
   CONSTRAINT ck_connector_grants_kind CHECK (grant_kind IN ('drive_folder', 'crm_object',
      'crm_field')),
   CONSTRAINT ck_connector_grants_resource CHECK (char_length(resource_id) BETWEEN 1 AND 300)
);
CREATE UNIQUE INDEX uq_connector_grants_live ON connector_grants
   (tenant_id, connection_id, grant_kind, resource_id) WHERE revoked_at IS NULL;
ALTER TABLE connector_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE connector_grants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON connector_grants TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON connector_grants TO app_rw;
GRANT UPDATE (revoked_at) ON connector_grants TO app_rw;

CREATE TABLE connector_quarantine (
   id              uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id       uuid NOT NULL DEFAULT app_tenant(),
   connection_id   uuid NOT NULL,
   resource_id     text NOT NULL,
   resource_kind   text NOT NULL,
   reason          text NOT NULL,
   status          text NOT NULL DEFAULT 'held',
   first_seen_at   timestamptz NOT NULL,
   decided_by      text,
   decided_at      timestamptz,
   CONSTRAINT pk_connector_quarantine PRIMARY KEY (id),
   CONSTRAINT fk_connector_quarantine_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT fk_connector_quarantine_connection FOREIGN KEY (tenant_id, connection_id)
      REFERENCES connector_connections (tenant_id, id),
   CONSTRAINT uq_connector_quarantine_resource UNIQUE (tenant_id, connection_id, resource_id),
   CONSTRAINT ck_connector_quarantine_kind CHECK (resource_kind IN ('folder', 'file',
      'shortcut', 'record')),
   CONSTRAINT ck_connector_quarantine_reason CHECK (reason IN ('outside_allow_list',
      'unknown_ancestry', 'shortcut_outside', 'field_not_allowed')),
   CONSTRAINT ck_connector_quarantine_status CHECK (status IN ('held', 'cleared', 'refused')
      AND ((status = 'held') = (decided_at IS NULL)))
);
ALTER TABLE connector_quarantine ENABLE ROW LEVEL SECURITY;
ALTER TABLE connector_quarantine FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON connector_quarantine TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON connector_quarantine TO app_rw;
GRANT UPDATE (status, decided_by, decided_at) ON connector_quarantine TO app_rw;

CREATE TABLE connector_items (
   id              bigserial NOT NULL,
   tenant_id       uuid NOT NULL DEFAULT app_tenant(),
   connection_id   uuid NOT NULL,
   resource_id     text NOT NULL,
   sha256          text NOT NULL,
   object_key      text NOT NULL,
   bytes           integer NOT NULL,
   state           text NOT NULL DEFAULT 'quarantined',
   fetched_at      timestamptz NOT NULL,
   CONSTRAINT pk_connector_items PRIMARY KEY (id),
   CONSTRAINT fk_connector_items_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT fk_connector_items_connection FOREIGN KEY (tenant_id, connection_id)
      REFERENCES connector_connections (tenant_id, id),
   CONSTRAINT uq_connector_items_content UNIQUE (tenant_id, connection_id, resource_id, sha256),
   CONSTRAINT ck_connector_items_sha CHECK (sha256 ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_connector_items_key CHECK (object_key ~ '^tenants/[0-9a-f-]{36}/connector-quarantine/'),
   CONSTRAINT ck_connector_items_state CHECK (state = 'quarantined'),
   CONSTRAINT ck_connector_items_bytes CHECK (bytes >= 0)
);
ALTER TABLE connector_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE connector_items FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON connector_items TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON connector_items TO app_rw;
GRANT USAGE ON SEQUENCE connector_items_id_seq TO app_rw;

CREATE TABLE connector_access_log (
   id              bigserial NOT NULL,
   tenant_id       uuid NOT NULL DEFAULT app_tenant(),
   connection_id   uuid NOT NULL,
   principal       text NOT NULL,
   kind            text NOT NULL,
   resource_id     text,
   purpose         text NOT NULL,
   outcome         text NOT NULL,
   at              timestamptz NOT NULL,
   CONSTRAINT pk_connector_access_log PRIMARY KEY (id),
   CONSTRAINT fk_connector_access_log_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_connector_access_log_purpose CHECK (purpose IN ('consent', 'token_exchange',
      'token_refresh', 'list', 'metadata', 'fetch', 'revoke', 'grant', 'clear')),
   CONSTRAINT ck_connector_access_log_outcome CHECK (outcome IN ('ok', 'denied', 'held', 'error')),
   CONSTRAINT ck_connector_access_log_resource CHECK (resource_id IS NULL
      OR char_length(resource_id) <= 300)
);
CREATE INDEX ix_connector_access_log_conn ON connector_access_log (tenant_id, connection_id, id);
ALTER TABLE connector_access_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE connector_access_log FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON connector_access_log TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON connector_access_log TO app_rw;
GRANT USAGE ON SEQUENCE connector_access_log_id_seq TO app_rw;
