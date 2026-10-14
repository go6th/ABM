-- Reverse 0070: the migration user owns the twelve definer functions again and the two NOLOGIN
-- roles lose their policies and grants (the roles stay: roles are shared by every database of the
-- cluster, as 0017 and 0021 leave theirs).
ALTER FUNCTION runs_orphaned(timestamptz) OWNER TO CURRENT_USER;
ALTER FUNCTION runs_stalled(timestamptz) OWNER TO CURRENT_USER;
ALTER FUNCTION research_files_orphaned(timestamptz) OWNER TO CURRENT_USER;
ALTER FUNCTION documents_orphaned(timestamptz) OWNER TO CURRENT_USER;
ALTER FUNCTION document_parts_orphaned(timestamptz) OWNER TO CURRENT_USER;
ALTER FUNCTION due_scheduled_refreshes(timestamptz) OWNER TO CURRENT_USER;
ALTER FUNCTION tenants_with_waiting_jobs() OWNER TO CURRENT_USER;
ALTER FUNCTION library_publication_counts() OWNER TO CURRENT_USER;
ALTER FUNCTION maildrive_connections_due(timestamptz) OWNER TO CURRENT_USER;
ALTER FUNCTION spend_runs_to_check(timestamptz) OWNER TO CURRENT_USER;
ALTER FUNCTION tenants_with_credit_activity(timestamptz) OWNER TO CURRENT_USER;
ALTER FUNCTION write_platform_tombstone(text, text, text, text, timestamptz) OWNER TO CURRENT_USER;

DROP POLICY IF EXISTS sweeper_read ON account_runs;
DROP POLICY IF EXISTS sweeper_read ON run_leases;
DROP POLICY IF EXISTS sweeper_read ON research_files;
DROP POLICY IF EXISTS sweeper_read ON documents;
DROP POLICY IF EXISTS sweeper_read ON document_parts;
DROP POLICY IF EXISTS sweeper_read ON accounts;
DROP POLICY IF EXISTS sweeper_read ON research_jobs;
DROP POLICY IF EXISTS sweeper_read ON library_publications;
DROP POLICY IF EXISTS sweeper_read ON connector_connections;
DROP POLICY IF EXISTS sweeper_read ON credit_entries;
REVOKE ALL ON account_runs, run_leases, research_files, documents, document_parts, accounts,
   research_jobs, library_publications, connector_connections, credit_entries FROM sweeper_definer;
REVOKE ALL ON platform_tombstones FROM tombstone_writer;
REVOKE ALL ON SEQUENCE platform_tombstones_id_seq FROM tombstone_writer;
