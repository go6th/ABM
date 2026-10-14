-- Reverse 0071: the IAM database users named in APP_RW_IAM_USERS leave app_rw.
DO $$
DECLARE
   name text;
BEGIN
   FOREACH name IN ARRAY string_to_array(coalesce(current_setting('app.rw_iam_users', true), ''), ',')
   LOOP
      name := btrim(name);
      CONTINUE WHEN name = '' OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = name);
      EXECUTE format('REVOKE app_rw FROM %I', name);
   END LOOP;
END $$;
