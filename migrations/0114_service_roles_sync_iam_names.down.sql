-- Undo migration 0114: service_roles_sync() as migration 0101 defined it.
CREATE OR REPLACE FUNCTION service_roles_sync() RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE
   api_no_access CONSTANT text[] := ARRAY[
      'run_leases', 'checkpoint_blobs', 'checkpoint_writes', 'checkpoint_migrations'];
   api_read_only CONSTANT text[] := ARRAY['tool_calls', 'checkpoints'];
   discovery CONSTANT text[] := ARRAY[
      'runs_orphaned', 'runs_stalled', 'documents_orphaned', 'document_parts_orphaned',
      'research_files_orphaned'];
   r record;
   pair record;
   name text;
BEGIN
   REVOKE ALL ON ALL TABLES IN SCHEMA public FROM app_api, app_sweeper;
   REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM app_api, app_sweeper;
   REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM app_api, app_sweeper;

   -- tables, views and sequences: app_rw's privileges minus the exclusions
   FOR r IN
      SELECT c.oid::regclass AS rel, c.relname, c.relkind, x.privilege_type AS priv
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) x
      WHERE n.nspname = 'public' AND x.grantee = 'app_rw'::regrole
        AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
   LOOP
      CONTINUE WHEN r.relname = ANY (api_no_access)
                 OR (r.relname = ANY (api_read_only) AND r.priv <> 'SELECT');
      EXECUTE format('GRANT %s ON %s %s TO app_api',
                     r.priv, CASE WHEN r.relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END, r.rel);
   END LOOP;

   -- column grants (an UPDATE limited to some columns stays limited)
   FOR r IN
      SELECT c.oid::regclass AS rel, c.relname, a.attname, x.privilege_type AS priv
      FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(a.attacl) x
      WHERE n.nspname = 'public' AND x.grantee = 'app_rw'::regrole AND NOT a.attisdropped
   LOOP
      CONTINUE WHEN r.relname = ANY (api_no_access)
                 OR (r.relname = ANY (api_read_only) AND r.priv <> 'SELECT');
      EXECUTE format('GRANT %s (%I) ON %s TO app_api', r.priv, r.attname, r.rel);
   END LOOP;

   -- functions app_rw may call (the definer-rights lookups); the sweeper's discovery is not
   -- the api's, and it is all app_sweeper may call
   FOR r IN
      SELECT p.oid::regprocedure AS fn, p.proname
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace, aclexplode(p.proacl) x
      WHERE n.nspname = 'public' AND x.grantee = 'app_rw'::regrole AND x.privilege_type = 'EXECUTE'
   LOOP
      IF r.proname = ANY (discovery) OR r.proname = 'operator_tenant_overview' THEN
         EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO app_sweeper', r.fn);
      END IF;
      CONTINUE WHEN r.proname = ANY (discovery);
      EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO app_api', r.fn);
   END LOOP;

   -- policies: app_api's copy of every app_rw policy, rebuilt from scratch
   FOR r IN
      SELECT schemaname, tablename, policyname FROM pg_policies
      WHERE schemaname = 'public' AND roles = ARRAY['app_api']::name[]
   LOOP
      EXECUTE format('DROP POLICY %I ON %I.%I', r.policyname, r.schemaname, r.tablename);
   END LOOP;
   FOR r IN
      SELECT schemaname, tablename, policyname, permissive, cmd, qual, with_check FROM pg_policies
      WHERE schemaname = 'public' AND 'app_rw' = ANY (roles)
   LOOP
      EXECUTE format('CREATE POLICY %I ON %I.%I AS %s FOR %s TO app_api%s%s',
                     left(r.policyname, 59) || '_api', r.schemaname, r.tablename,
                     r.permissive, r.cmd,
                     coalesce(' USING (' || r.qual || ')', ''),
                     coalesce(' WITH CHECK (' || r.with_check || ')', ''));
   END LOOP;

   -- the IAM database users move from app_rw to their service role
   FOR pair IN SELECT * FROM (VALUES ('app_api', 'app.api_iam_users'),
                                     ('app_worker', 'app.worker_iam_users')) AS v(role, setting)
   LOOP
      FOREACH name IN ARRAY string_to_array(coalesce(current_setting(pair.setting, true), ''), ',')
      LOOP
         name := btrim(name);
         CONTINUE WHEN name = '' OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = name);
         IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = name AND (rolsuper OR rolbypassrls)) THEN
            RAISE EXCEPTION '% names a superuser or BYPASSRLS role', pair.setting;
         END IF;
         EXECUTE format('GRANT %I TO %I WITH INHERIT TRUE', pair.role, name);
         IF EXISTS (SELECT 1 FROM pg_auth_members
                    WHERE roleid = 'app_rw'::regrole AND member = name::regrole) THEN
            EXECUTE format('REVOKE app_rw FROM %I', name);
         END IF;
      END LOOP;
   END LOOP;
END $$;
REVOKE ALL ON FUNCTION service_roles_sync() FROM PUBLIC;
