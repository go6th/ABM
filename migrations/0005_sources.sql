-- Data layer v1, step 9: sources with stable per-account codes.
-- Every external item ever seen for an account. source_code (S-0001) is assigned once,
-- reused on every later run and refresh, never reassigned and never renumbered.

CREATE TABLE sources (
   id                uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id         uuid NOT NULL DEFAULT app_tenant(),
   account_id        uuid NOT NULL,
   source_code       text NOT NULL,
   url               text NOT NULL,
   url_normalized    text NOT NULL,
   url_hash          char(64) NOT NULL,
   title             text,
   publisher         text,
   published_at      date,
   kind              text NOT NULL,
   first_seen_run_id uuid NOT NULL,
   last_seen_run_id  uuid NOT NULL,
   created_at        timestamptz NOT NULL DEFAULT now(),
   updated_at        timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_sources PRIMARY KEY (id),
   CONSTRAINT uq_sources_account_id_source_code UNIQUE (account_id, source_code),
   CONSTRAINT uq_sources_account_id_url_hash UNIQUE (account_id, url_hash),
   CONSTRAINT uq_sources_tenant_id_account_id_id UNIQUE (tenant_id, account_id, id),
   CONSTRAINT fk_sources_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT fk_sources_tenant_id_account_id_accounts
      FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
   -- Seen-in runs must be runs of the same account.
   CONSTRAINT fk_sources_tenant_id_account_id_first_seen_run_id_runs
      FOREIGN KEY (tenant_id, account_id, first_seen_run_id) REFERENCES runs (tenant_id, account_id, id),
   CONSTRAINT fk_sources_tenant_id_account_id_last_seen_run_id_runs
      FOREIGN KEY (tenant_id, account_id, last_seen_run_id) REFERENCES runs (tenant_id, account_id, id),
   CONSTRAINT ck_sources_source_code_format CHECK (source_code ~ '^S-[0-9]{4,}$'),
   CONSTRAINT ck_sources_kind CHECK (kind IN ('primary', 'secondary', 'aggregator')),
   CONSTRAINT ck_sources_url_is_http CHECK (url ~* '^https?://\S+$'),
   CONSTRAINT ck_sources_url_normalized_is_http CHECK (url_normalized ~ '^https?://\S+$'),
   -- The database proves the dedupe key: url_hash is the sha256 of url_normalized.
   CONSTRAINT ck_sources_url_hash_matches CHECK (
      url_hash = encode(sha256(convert_to(url_normalized, 'UTF8')), 'hex')
   )
);
CREATE INDEX ix_sources_first_seen_run_id ON sources (first_seen_run_id);
CREATE INDEX ix_sources_last_seen_run_id ON sources (last_seen_run_id);

-- A source keeps its identity forever. Only descriptive metadata and last_seen_run_id change.
CREATE FUNCTION sources_guard_identity() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
   IF TG_OP = 'DELETE' THEN
      RAISE EXCEPTION 'sources: rows are never deleted (source %)', OLD.source_code
         USING ERRCODE = 'restrict_violation';
   END IF;
   IF NEW.source_code <> OLD.source_code OR NEW.url_hash <> OLD.url_hash
      OR NEW.url_normalized <> OLD.url_normalized OR NEW.account_id <> OLD.account_id
      OR NEW.tenant_id <> OLD.tenant_id OR NEW.first_seen_run_id <> OLD.first_seen_run_id THEN
      RAISE EXCEPTION 'sources: source_code, url and first_seen_run_id are immutable (source %)',
         OLD.source_code USING ERRCODE = 'check_violation';
   END IF;
   RETURN NEW;
END $$;
CREATE TRIGGER sources_guard_identity BEFORE UPDATE OR DELETE ON sources
   FOR EACH ROW EXECUTE FUNCTION sources_guard_identity();

ALTER TABLE sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE sources FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON sources TO app_rw
   USING      (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE ON sources TO app_rw;       -- no DELETE: sources are permanent
