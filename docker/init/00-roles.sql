-- docker/init/00-roles.sql
-- Executed once by the postgres docker-entrypoint at first start (POSTGRES_USER=postgres).
--
-- Role model (ADR-011, Spec ch.4):
--   postgres     : owner / migrator. DDL only. Never used by the application at runtime.
--   app_rw       : runtime role for API + workers. NOBYPASSRLS, does not own tables.
--   public_reader: role used only by render_public inside a SET LOCAL ROLE switch; it can
--                  only see rows whose visibility = 'public'. It has NO grant on private data
--                  tables (chunks, documents), which is the two-output rule at the DB layer.
--   migrator     : reserved for CI migrations; DDL only.

CREATE ROLE app_rw LOGIN PASSWORD 'app_rw_dev' NOBYPASSRLS;
COMMENT ON ROLE app_rw IS 'Runtime role: API + workers. Non-owner, NOBYPASSRLS (I3).';

CREATE ROLE public_reader NOLOGIN;
COMMENT ON ROLE public_reader IS 'Renderer role: may SELECT public rows only (two-output rule).';

GRANT public_reader TO postgres WITH ADMIN OPTION;
GRANT public_reader TO app_rw;

-- Default privileges: future objects created by postgres get runtime grants automatically.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    GRANT USAGE ON SEQUENCES TO app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    GRANT SELECT ON TABLES TO public_reader;
