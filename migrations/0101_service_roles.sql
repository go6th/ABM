-- Migration 0101 (2026-11-03, ADR-0123): the runtime role app_rw is split by service.
--
-- One app_rw role served the api, the worker and the jobs, so the api process held every right
-- the research pipeline needs. Three NOLOGIN, NOBYPASSRLS group roles narrow that; the Cloud SQL
-- IAM users (which log in) join exactly one of them, the way they joined app_rw in 0071:
--
--   app_api      app_rw's rights MINUS: every write on tool_calls (SELECT stays: the run
--                counters read it), every right on run_leases, checkpoint_blobs,
--                checkpoint_writes and checkpoint_migrations, every write on checkpoints (SELECT
--                stays: the run steps panel and the live page read the finished nodes), and
--                EXECUTE on the sweeper's tenant-less discovery lookups. It gets its own copy
--                of every app_rw policy (named <policy>_api, same command, kind and predicates).
--   app_worker   a member of app_rw (INHERIT, no SET): the research slices, the in-process
--                sweeper and the jobs keep exactly app_rw's rights and policies.
--   app_sweeper  EXECUTE on the sweeper's discovery lookups and operator_tenant_overview(), and
--                nothing else. No service signs in as it yet: the in-process sweeper acts on
--                tenant tables after discovery, so it runs as app_worker (ADR-0123); the role
--                is for a sweeper split out as its own job.
--
-- app_rw itself is unchanged (the local stack, the tests, and any cloud user not moved yet). Its
-- policies are not touched (check_rls_catalog.sql check 7 wants an app_rw-only policy on every
-- tenant table). Tables keep FORCE ROW LEVEL SECURITY.
--
-- service_roles_sync() derives app_api's grants and policy copies from app_rw's catalog
-- entries, so the exclusions below are the only hand-written list, and moves the IAM users named
-- in the transaction-local settings app.api_iam_users and app.worker_iam_users (from
-- APP_API_IAM_USERS and APP_WORKER_IAM_USERS on the migrate job; empty locally, a no-op) from
-- app_rw to their role. migrations/env.py calls it after every migration run, so a later
-- migration that grants app_rw or adds an app_rw policy reaches app_api by itself;
-- tests/isolation/test_service_roles.py fails when the two drift. It has no definer rights: it
-- runs with the migrator's own rights, and only the migrator may call it.
-- Rollback: 0101_service_roles.down.sql (the roles stay: they are cluster-global).

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_api') THEN
    CREATE ROLE app_api NOLOGIN NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_worker') THEN
    CREATE ROLE app_worker NOLOGIN NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_sweeper') THEN
    CREATE ROLE app_sweeper NOLOGIN NOBYPASSRLS;
  END IF;
END $$;
-- a role of that name that already exists (roles are cluster-global) must have the same
-- attributes; changing the superuser or RLS-bypass attribute of a role needs a superuser, which
-- Cloud SQL's migration user is not, so this checks instead of altering
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ('app_api', 'app_worker', 'app_sweeper')
             AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb)) THEN
    RAISE EXCEPTION 'app_api, app_worker and app_sweeper must be NOLOGIN group roles without'
                    ' SUPERUSER, BYPASSRLS, CREATEROLE or CREATEDB';
  END IF;
END $$;

GRANT app_rw TO app_worker WITH INHERIT TRUE, SET FALSE;
-- render_public and the export readers switch to public_reader with SET LOCAL ROLE, as app_rw
-- does (0002); a SET ROLE needs the SET option on every grant of the chain
GRANT public_reader TO app_api, app_worker WITH INHERIT FALSE, SET TRUE;

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

SELECT service_roles_sync();
