-- Rollback of 0096_purge_deleted_account.sql (development only; production never contracts in the
-- same release). The three roles stay: roles are cluster-global and other databases may use them.
DROP FUNCTION IF EXISTS purge_deleted_account(uuid, uuid, boolean, text, boolean);
DROP FUNCTION IF EXISTS purge_candidates(text);
DROP POLICY IF EXISTS purge_lookup_read ON accounts;
DROP TRIGGER IF EXISTS accounts_tombstone_guard ON accounts;
DROP FUNCTION IF EXISTS accounts_tombstone_guard();

-- sources_guard_identity as it was before: rows are never deleted by anyone
CREATE OR REPLACE FUNCTION sources_guard_identity() RETURNS trigger LANGUAGE plpgsql AS $$
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

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM account_purger, account_purge_lookup;
REVOKE app_rw FROM account_purger;
REVOKE account_purge_runner FROM CURRENT_USER;
