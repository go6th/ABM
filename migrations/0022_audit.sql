-- Migration 0022: audit (Phase 3 WS3; expand only). Spec docs/prompts/build_phase3.md s5, AU1, AU2.
-- down_revision 0021.
--
-- audit_events: insert-only (no UPDATE or DELETE grant), one hash chain per tenant: each row's
--   hash is SHA-256 over the previous row's hash and this row's canonical fields, and seq is
--   gapless per tenant (unique). Identifiers, categories and outcomes only: never content, never
--   a token, a provider name or a cost. dedupe_key (unique per tenant when present) makes a
--   repeated read by the same actor of the same resource within the dedupe window one row,
--   across every API instance. Writers serialise per tenant on a transaction-scoped advisory
--   lock (audit.events.record), so concurrent writers cannot fork the chain.
-- audit_chain_checks: insert-only results of the chain verification job.
-- Rollback: 0022_audit.down.sql.

CREATE TABLE audit_events (
   id                bigserial NOT NULL,
   tenant_id         uuid NOT NULL DEFAULT app_tenant(),
   seq               bigint NOT NULL,
   at                timestamptz NOT NULL,
   actor             text NOT NULL,
   actor_kind        text NOT NULL,
   audit_class       text NOT NULL,
   action            text NOT NULL,
   resource_type     text NOT NULL,
   resource_id       text,
   route             text,
   request_id        text,
   outcome           text NOT NULL,
   support_grant_id  uuid,
   dedupe_key        text,
   prev_hash         text NOT NULL,
   hash              text NOT NULL,
   CONSTRAINT pk_audit_events PRIMARY KEY (id),
   CONSTRAINT fk_audit_events_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT uq_audit_events_seq UNIQUE (tenant_id, seq),
   CONSTRAINT uq_audit_events_dedupe UNIQUE (tenant_id, dedupe_key),
   CONSTRAINT ck_audit_events_seq CHECK (seq >= 1),
   CONSTRAINT ck_audit_events_actor CHECK (char_length(actor) BETWEEN 1 AND 300),
   CONSTRAINT ck_audit_events_kind CHECK (actor_kind IN ('user', 'operator', 'support',
      'system', 'idp')),
   CONSTRAINT ck_audit_events_class CHECK (audit_class IN ('read', 'run', 'export', 'admin',
      'access')),
   CONSTRAINT ck_audit_events_action CHECK (action ~ '^[a-z_]+(\.[a-z_]+){1,3}$'),
   CONSTRAINT ck_audit_events_resource CHECK (resource_type ~ '^[a-z_]{1,40}$'
      AND (resource_id IS NULL OR char_length(resource_id) <= 300)),
   CONSTRAINT ck_audit_events_route CHECK (route IS NULL OR char_length(route) <= 200),
   CONSTRAINT ck_audit_events_outcome CHECK (outcome IN ('ok', 'denied', 'error')),
   CONSTRAINT ck_audit_events_hashes CHECK (prev_hash ~ '^[0-9a-f]{64}$'
      AND hash ~ '^[0-9a-f]{64}$')
);
CREATE INDEX ix_audit_events_actor ON audit_events (tenant_id, actor, seq);
CREATE INDEX ix_audit_events_resource ON audit_events (tenant_id, resource_type, resource_id);
ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON audit_events TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON audit_events TO app_rw;
GRANT USAGE ON SEQUENCE audit_events_id_seq TO app_rw;

CREATE TABLE audit_chain_checks (
   id              bigserial NOT NULL,
   tenant_id       uuid NOT NULL DEFAULT app_tenant(),
   from_seq        bigint NOT NULL,
   to_seq          bigint NOT NULL,
   ok              boolean NOT NULL,
   first_bad_seq   bigint,
   reason          text,
   at              timestamptz NOT NULL,
   CONSTRAINT pk_audit_chain_checks PRIMARY KEY (id),
   CONSTRAINT fk_audit_chain_checks_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_audit_chain_checks_range CHECK (from_seq >= 0 AND to_seq >= from_seq),
   CONSTRAINT ck_audit_chain_checks_reason CHECK (reason IS NULL OR reason ~ '^[a-z_]{1,40}$'),
   CONSTRAINT ck_audit_chain_checks_ok CHECK (ok = (first_bad_seq IS NULL))
);
ALTER TABLE audit_chain_checks ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_chain_checks FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON audit_chain_checks TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON audit_chain_checks TO app_rw;
GRANT USAGE ON SEQUENCE audit_chain_checks_id_seq TO app_rw;
