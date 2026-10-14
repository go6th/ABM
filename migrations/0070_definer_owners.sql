-- Cross-tenant definer functions get NOLOGIN owners (security audit M1, 2026-10-14).
--
-- Eleven security-definer lookups used by the sweepers, the schedules tick, the Mail and drive
-- sync and the spend checks, plus write_platform_tombstone, were owned by the migration login
-- role. On the replica that role is a superuser, so the functions saw every tenant. On Cloud SQL
-- the migration user is not a superuser: under FORCE RLS, with policies only TO app_rw, the
-- lookups would return no rows and the sweepers would silently do nothing; and if the migration
-- user had BYPASSRLS, cross-tenant reads would rest on a LOGIN role whose credential sits in
-- Secret Manager. As in 0017 (governor_definer) and 0021 (identity_resolver), each function now
-- belongs to a NOLOGIN, NOBYPASSRLS, NOINHERIT role that holds exactly what its functions read:
--
--   sweeper_definer   a SELECT policy USING (true) on the eleven tables the lookups read, and
--                     column grants on the columns they use (identifiers, statuses, times)
--   tombstone_writer  INSERT on platform_tombstones (no tenant table, no read path)
--
-- The functions still return identifiers only, still pin search_path, and EXECUTE stays app_rw's
-- alone. Changing an owner needs the migrating role to be able to SET ROLE to the new owner and
-- the new owner to hold CREATE on the schema (a superuser skips both checks, Cloud SQL's
-- migration user does not): both are granted for the ALTER statements only and revoked after.
-- Rollback: 0070_definer_owners.down.sql (the migration user owns the functions again).

DO $$ BEGIN
   IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sweeper_definer') THEN
      CREATE ROLE sweeper_definer NOLOGIN NOBYPASSRLS NOINHERIT;
   END IF;
   IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tombstone_writer') THEN
      CREATE ROLE tombstone_writer NOLOGIN NOBYPASSRLS NOINHERIT;
   END IF;
END $$;

-- What the eleven lookups read, and nothing more.
CREATE POLICY sweeper_read ON account_runs FOR SELECT TO sweeper_definer USING (true);
CREATE POLICY sweeper_read ON run_leases FOR SELECT TO sweeper_definer USING (true);
CREATE POLICY sweeper_read ON research_files FOR SELECT TO sweeper_definer USING (true);
CREATE POLICY sweeper_read ON documents FOR SELECT TO sweeper_definer USING (true);
CREATE POLICY sweeper_read ON document_parts FOR SELECT TO sweeper_definer USING (true);
CREATE POLICY sweeper_read ON accounts FOR SELECT TO sweeper_definer USING (true);
CREATE POLICY sweeper_read ON research_jobs FOR SELECT TO sweeper_definer USING (true);
CREATE POLICY sweeper_read ON library_publications FOR SELECT TO sweeper_definer USING (true);
CREATE POLICY sweeper_read ON connector_connections FOR SELECT TO sweeper_definer USING (true);
CREATE POLICY sweeper_read ON credit_entries FOR SELECT TO sweeper_definer USING (true);
GRANT SELECT (tenant_id, id, status, created_at, stalled_at, progress_at) ON account_runs
   TO sweeper_definer;
GRANT SELECT (run_id, expires_at, heartbeat_at) ON run_leases TO sweeper_definer;
GRANT SELECT (tenant_id, id, status, lease_expires_at, updated_at) ON research_files
   TO sweeper_definer;
GRANT SELECT (tenant_id, id, status, lease_expires_at, updated_at) ON documents
   TO sweeper_definer;
GRANT SELECT (tenant_id, document_id, part_no, status, lease_expires_at, updated_at)
   ON document_parts TO sweeper_definer;
GRANT SELECT (tenant_id, id, refresh_cadence, next_refresh_at) ON accounts TO sweeper_definer;
GRANT SELECT (tenant_id, account_id, status, created_at, finished_at) ON research_jobs
   TO sweeper_definer;
GRANT SELECT (library_account_id, revoked_at) ON library_publications TO sweeper_definer;
GRANT SELECT (tenant_id, id, status, owner_user_id, kind, last_synced_at)
   ON connector_connections TO sweeper_definer;
GRANT SELECT (tenant_id, at) ON credit_entries TO sweeper_definer;

-- The tombstone writer inserts one content-free platform row (the conflict target is read).
GRANT SELECT (subject_type, subject_id), INSERT (subject_type, subject_id, reason, content_sha256, at)
   ON platform_tombstones TO tombstone_writer;
GRANT USAGE ON SEQUENCE platform_tombstones_id_seq TO tombstone_writer;

-- Hand the functions over. A migrating role that is not a superuser gets the two rights an owner
-- change needs for these statements only (a superuser skips both checks, and roles are shared by
-- every database of the cluster, so the replica's concurrent test databases leave them alone).
DO $$ BEGIN
   IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
      EXECUTE format('GRANT sweeper_definer, tombstone_writer TO %I', current_user);
      GRANT CREATE ON SCHEMA public TO sweeper_definer, tombstone_writer;
   END IF;
END $$;
ALTER FUNCTION runs_orphaned(timestamptz) OWNER TO sweeper_definer;
ALTER FUNCTION runs_stalled(timestamptz) OWNER TO sweeper_definer;
ALTER FUNCTION research_files_orphaned(timestamptz) OWNER TO sweeper_definer;
ALTER FUNCTION documents_orphaned(timestamptz) OWNER TO sweeper_definer;
ALTER FUNCTION document_parts_orphaned(timestamptz) OWNER TO sweeper_definer;
ALTER FUNCTION due_scheduled_refreshes(timestamptz) OWNER TO sweeper_definer;
ALTER FUNCTION tenants_with_waiting_jobs() OWNER TO sweeper_definer;
ALTER FUNCTION library_publication_counts() OWNER TO sweeper_definer;
ALTER FUNCTION maildrive_connections_due(timestamptz) OWNER TO sweeper_definer;
ALTER FUNCTION spend_runs_to_check(timestamptz) OWNER TO sweeper_definer;
ALTER FUNCTION tenants_with_credit_activity(timestamptz) OWNER TO sweeper_definer;
ALTER FUNCTION write_platform_tombstone(text, text, text, text, timestamptz)
   OWNER TO tombstone_writer;
DO $$ BEGIN
   IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
      REVOKE CREATE ON SCHEMA public FROM sweeper_definer, tombstone_writer;
      EXECUTE format('REVOKE sweeper_definer, tombstone_writer FROM %I', current_user);
   END IF;
END $$;

-- An owner keeps EXECUTE on its own function; PUBLIC stays revoked and app_rw keeps EXECUTE.
REVOKE ALL ON FUNCTION runs_orphaned(timestamptz), runs_stalled(timestamptz),
   research_files_orphaned(timestamptz), documents_orphaned(timestamptz),
   document_parts_orphaned(timestamptz), due_scheduled_refreshes(timestamptz),
   tenants_with_waiting_jobs(), library_publication_counts(), maildrive_connections_due(timestamptz),
   spend_runs_to_check(timestamptz), tenants_with_credit_activity(timestamptz),
   write_platform_tombstone(text, text, text, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION runs_orphaned(timestamptz), runs_stalled(timestamptz),
   research_files_orphaned(timestamptz), documents_orphaned(timestamptz),
   document_parts_orphaned(timestamptz), due_scheduled_refreshes(timestamptz),
   tenants_with_waiting_jobs(), library_publication_counts(), maildrive_connections_due(timestamptz),
   spend_runs_to_check(timestamptz), tenants_with_credit_activity(timestamptz),
   write_platform_tombstone(text, text, text, text, timestamptz) TO app_rw;
