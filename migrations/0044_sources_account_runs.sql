-- Account-level run lifecycle integration for stable source IDs.
-- The original source registry points at ``runs``; new account runs may be created without a
-- legacy mirror (e.g. direct worker/test integrations), so retain a tenant/account-scoped FK for
-- either lifecycle while keeping exactly one first-seen and one last-seen reference.

ALTER TABLE account_runs
   ADD CONSTRAINT uq_account_runs_tenant_account_id UNIQUE (tenant_id, account_id, id);

ALTER TABLE sources
   ALTER COLUMN first_seen_run_id DROP NOT NULL,
   ALTER COLUMN last_seen_run_id DROP NOT NULL,
   ADD COLUMN first_seen_account_run_id uuid,
   ADD COLUMN last_seen_account_run_id uuid,
   ADD CONSTRAINT fk_sources_tenant_id_account_id_first_seen_account_run_id_account_runs
      FOREIGN KEY (tenant_id, account_id, first_seen_account_run_id)
      REFERENCES account_runs (tenant_id, account_id, id),
   ADD CONSTRAINT fk_sources_tenant_id_account_id_last_seen_account_run_id_account_runs
      FOREIGN KEY (tenant_id, account_id, last_seen_account_run_id)
      REFERENCES account_runs (tenant_id, account_id, id),
   ADD CONSTRAINT ck_sources_one_first_seen_run
      CHECK (num_nonnulls(first_seen_run_id, first_seen_account_run_id) = 1),
   ADD CONSTRAINT ck_sources_one_last_seen_run
      CHECK (num_nonnulls(last_seen_run_id, last_seen_account_run_id) = 1);

CREATE INDEX ix_sources_first_seen_account_run_id ON sources (first_seen_account_run_id);
CREATE INDEX ix_sources_last_seen_account_run_id ON sources (last_seen_account_run_id);

CREATE OR REPLACE FUNCTION sources_guard_identity() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
   IF TG_OP = 'DELETE' THEN
      RAISE EXCEPTION 'sources: rows are never deleted (source %)', OLD.source_code
         USING ERRCODE = 'restrict_violation';
   END IF;
   IF NEW.source_code <> OLD.source_code OR NEW.url_hash <> OLD.url_hash
      OR NEW.url_normalized <> OLD.url_normalized OR NEW.account_id <> OLD.account_id
      OR NEW.tenant_id <> OLD.tenant_id
      OR NEW.first_seen_run_id IS DISTINCT FROM OLD.first_seen_run_id
      OR NEW.first_seen_account_run_id IS DISTINCT FROM OLD.first_seen_account_run_id THEN
      RAISE EXCEPTION 'sources: source identity and first-seen references are immutable (source %)',
         OLD.source_code USING ERRCODE = 'check_violation';
   END IF;
   RETURN NEW;
END $$;
