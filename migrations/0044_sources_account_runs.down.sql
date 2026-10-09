-- Rollback of migration 0044; refuse to discard account-run source provenance.
DO $$
BEGIN
   IF EXISTS (
      SELECT 1 FROM sources
      WHERE first_seen_account_run_id IS NOT NULL OR last_seen_account_run_id IS NOT NULL
   ) THEN
      RAISE EXCEPTION 'cannot downgrade source run references while account-run sources exist';
   END IF;
END $$;

DROP INDEX IF EXISTS ix_sources_last_seen_account_run_id;
DROP INDEX IF EXISTS ix_sources_first_seen_account_run_id;
ALTER TABLE sources
   DROP CONSTRAINT IF EXISTS ck_sources_one_last_seen_run,
   DROP CONSTRAINT IF EXISTS ck_sources_one_first_seen_run,
   DROP CONSTRAINT IF EXISTS fk_sources_tenant_id_account_id_last_seen_account_run_id_account_runs,
   DROP CONSTRAINT IF EXISTS fk_sources_tenant_id_account_id_first_seen_account_run_id_account_runs,
   DROP COLUMN IF EXISTS last_seen_account_run_id,
   DROP COLUMN IF EXISTS first_seen_account_run_id,
   ALTER COLUMN last_seen_run_id SET NOT NULL,
   ALTER COLUMN first_seen_run_id SET NOT NULL;
ALTER TABLE account_runs DROP CONSTRAINT IF EXISTS uq_account_runs_tenant_account_id;

CREATE OR REPLACE FUNCTION sources_guard_identity() RETURNS trigger
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
