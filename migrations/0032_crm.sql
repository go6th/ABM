-- Migration 0032: CRM write-back (Phase 4 WS7; expand only). Spec docs/prompts/build_phase4.md
-- s5, CW1 to CW5, D-77, C28. down_revision 0031.
--
-- crm_connection_events: insert-only enable and revoke events per CRM (the newest is the state;
--   revoking stops writes at once). Tokens never live here (secret references elsewhere).
-- crm_mappings: insert-only, human-confirmed account -> CRM record mappings (newest wins).
-- crm_writes: insert-only write intents, unique (account, crm, kind, content hash): a replay or a
--   repeated request adds nothing. Carries the marker written into the created record.
-- crm_write_events: insert-only lifecycle of a write: requested, approved, sending, created,
--   adopted (found by its marker after a crash), refused, failed. One create attempt per write.
-- Rollback: 0032_crm.down.sql.

CREATE TABLE crm_connection_events (
   id          bigserial NOT NULL,
   tenant_id   uuid NOT NULL DEFAULT app_tenant(),
   crm         text NOT NULL,
   event       text NOT NULL,
   actor       text NOT NULL,
   at          timestamptz NOT NULL,
   CONSTRAINT pk_crm_connection_events PRIMARY KEY (id),
   CONSTRAINT fk_crm_connection_events_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_crm_connection_events_crm CHECK (crm IN ('salesforce', 'hubspot')),
   CONSTRAINT ck_crm_connection_events_event CHECK (event IN ('enabled', 'revoked'))
);
ALTER TABLE crm_connection_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_connection_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON crm_connection_events TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON crm_connection_events TO app_rw;
GRANT USAGE ON SEQUENCE crm_connection_events_id_seq TO app_rw;

CREATE TABLE crm_mappings (
   tenant_id      uuid NOT NULL DEFAULT app_tenant(),
   account_id     uuid NOT NULL,
   crm            text NOT NULL,
   version        integer NOT NULL,
   record_id      text NOT NULL,
   confirmed_by   text NOT NULL,
   created_at     timestamptz NOT NULL,
   CONSTRAINT pk_crm_mappings PRIMARY KEY (tenant_id, account_id, crm, version),
   CONSTRAINT fk_crm_mappings_account FOREIGN KEY (tenant_id, account_id)
      REFERENCES accounts (tenant_id, id),
   CONSTRAINT ck_crm_mappings_crm CHECK (crm IN ('salesforce', 'hubspot')),
   CONSTRAINT ck_crm_mappings_record CHECK (record_id ~ '^[A-Za-z0-9_-]{1,64}$' AND version >= 1)
);
ALTER TABLE crm_mappings ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_mappings FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON crm_mappings TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON crm_mappings TO app_rw;

CREATE TABLE crm_writes (
   id             uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id      uuid NOT NULL DEFAULT app_tenant(),
   account_id     uuid NOT NULL,
   crm            text NOT NULL,
   kind           text NOT NULL,
   content_hash   text NOT NULL,
   record_id      text NOT NULL,
   title          text NOT NULL,
   body           text NOT NULL,
   marker         text NOT NULL,
   requested_by   text NOT NULL,
   created_at     timestamptz NOT NULL,
   CONSTRAINT pk_crm_writes PRIMARY KEY (id),
   CONSTRAINT fk_crm_writes_account FOREIGN KEY (tenant_id, account_id)
      REFERENCES accounts (tenant_id, id),
   CONSTRAINT uq_crm_writes_key UNIQUE (tenant_id, account_id, crm, kind, content_hash),
   CONSTRAINT uq_crm_writes_marker UNIQUE (tenant_id, marker),
   CONSTRAINT ck_crm_writes_crm CHECK (crm IN ('salesforce', 'hubspot')),
   CONSTRAINT ck_crm_writes_kind CHECK (kind IN ('note', 'task', 'account_snapshot')),
   CONSTRAINT ck_crm_writes_hash CHECK (content_hash ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_crm_writes_text CHECK (char_length(title) BETWEEN 1 AND 120
      AND char_length(body) BETWEEN 1 AND 4200)
);
ALTER TABLE crm_writes ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_writes FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON crm_writes TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON crm_writes TO app_rw;

CREATE TABLE crm_write_events (
   id           bigserial NOT NULL,
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   write_id     uuid NOT NULL,
   event        text NOT NULL,
   actor        text NOT NULL,
   created_record_id text,
   reason       text,
   at           timestamptz NOT NULL,
   CONSTRAINT pk_crm_write_events PRIMARY KEY (id),
   CONSTRAINT fk_crm_write_events_write FOREIGN KEY (write_id) REFERENCES crm_writes (id),
   CONSTRAINT ck_crm_write_events_event CHECK (event IN ('requested', 'approved', 'sending',
      'created', 'adopted', 'refused', 'failed')),
   CONSTRAINT ck_crm_write_events_reason CHECK (reason IS NULL OR reason ~ '^[a-z_]{1,40}$')
);
-- one terminal outcome and one create attempt per write
CREATE UNIQUE INDEX uq_crm_write_events_once ON crm_write_events (write_id, event)
   WHERE event IN ('approved', 'sending', 'created', 'adopted', 'refused', 'failed');
ALTER TABLE crm_write_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_write_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON crm_write_events TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON crm_write_events TO app_rw;
GRANT USAGE ON SEQUENCE crm_write_events_id_seq TO app_rw;
