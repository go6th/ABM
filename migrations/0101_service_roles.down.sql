-- Reverse 0101: app_api loses its grants and policy copies, app_sweeper its EXECUTE grants, and
-- the sync function goes. The roles and their memberships stay: roles are cluster-global and
-- may serve other databases of the cluster. IAM users that 0101 moved are not put back into
-- app_rw here; rerun 0071 (APP_RW_IAM_USERS) for that.
DO $$
DECLARE
   r record;
BEGIN
   FOR r IN
      SELECT schemaname, tablename, policyname FROM pg_policies
      WHERE schemaname = 'public' AND roles = ARRAY['app_api']::name[]
   LOOP
      EXECUTE format('DROP POLICY %I ON %I.%I', r.policyname, r.schemaname, r.tablename);
   END LOOP;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM app_api, app_sweeper;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM app_api, app_sweeper;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM app_api, app_sweeper;
DROP FUNCTION IF EXISTS service_roles_sync();
