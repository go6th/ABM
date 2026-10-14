-- The cloud runtime signs in as Cloud SQL IAM database users (api, worker), which must be members
-- of app_rw and nothing more (security audit L16; Agent 3's design). The names come from
-- APP_RW_IAM_USERS on the migrate job, handed to this file as the transaction-local setting
-- app.rw_iam_users (comma separated; empty locally, where this is a no-op). A name whose role does
-- not exist yet is skipped (Terraform creates the IAM users; the next migrate run grants them).
-- A role that is a superuser or bypasses RLS stops the migration: it must never sit behind the
-- runtime's policies. Membership inherits app_rw's table rights; nothing is owned or granted
-- otherwise. Idempotent: granting an existing membership again changes nothing.
-- Rollback: 0071_app_rw_iam_users.down.sql.
DO $$
DECLARE
   name text;
BEGIN
   FOREACH name IN ARRAY string_to_array(coalesce(current_setting('app.rw_iam_users', true), ''), ',')
   LOOP
      name := btrim(name);
      CONTINUE WHEN name = '' OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = name);
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = name AND (rolsuper OR rolbypassrls)) THEN
         RAISE EXCEPTION 'APP_RW_IAM_USERS names a superuser or BYPASSRLS role';
      END IF;
      EXECUTE format('GRANT app_rw TO %I WITH INHERIT TRUE', name);
   END LOOP;
END $$;
