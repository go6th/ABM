-- Demonstrates the public_reader leak (reproduced on PostgreSQL 16.13).
-- Run on a SCRATCH server as a superuser:  psql -h <host> -U <super> -v admin=<super> -d postgres -f public_reader_leak_demo.sql
-- Expected output: the first public_reader query shows BOTH rows (leak); after scoping the tenant policy
-- TO app_rw, the second public_reader query shows only the public row.
\set ON_ERROR_STOP off
DROP DATABASE IF EXISTS rlst; CREATE DATABASE rlst;
\c rlst
DROP ROLE IF EXISTS app_rw; DROP ROLE IF EXISTS public_reader;
CREATE ROLE app_rw LOGIN NOINHERIT NOBYPASSRLS;
CREATE ROLE public_reader NOLOGIN;
GRANT public_reader TO app_rw;
CREATE FUNCTION app_tenant() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid $$;
CREATE TABLE facts (id serial primary key, tenant_id uuid not null, visibility text not null, v text);
ALTER TABLE facts ENABLE ROW LEVEL SECURITY; ALTER TABLE facts FORCE ROW LEVEL SECURITY;
-- pattern used for every table in ARCH 18.3 (no TO clause)
CREATE POLICY tenant_isolation ON facts USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
-- ARCH 18.3 two-output policy
CREATE POLICY public_only ON facts FOR SELECT TO public_reader USING (tenant_id = (SELECT app_tenant()) AND visibility = 'public');
GRANT SELECT, INSERT ON facts TO app_rw; GRANT SELECT ON facts TO public_reader; GRANT USAGE ON SEQUENCE facts_id_seq TO app_rw;
INSERT INTO facts(tenant_id, visibility, v) VALUES ('11111111-1111-1111-1111-111111111111','public','pub'),('11111111-1111-1111-1111-111111111111','private','SECRET');
\c rlst app_rw
BEGIN; SELECT set_config('app.tenant_id','11111111-1111-1111-1111-111111111111', true);
\echo --- as app_rw (expect both rows)
SELECT visibility, v FROM facts ORDER BY id;
SET LOCAL ROLE public_reader;
\echo --- as public_reader, tenant policy unscoped (LEAK: the private row is visible)
SELECT visibility, v FROM facts ORDER BY id;
COMMIT;
\c rlst :admin
DROP POLICY tenant_isolation ON facts;
CREATE POLICY tenant_isolation ON facts TO app_rw USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
\c rlst app_rw
BEGIN; SELECT set_config('app.tenant_id','11111111-1111-1111-1111-111111111111', true);
SET LOCAL ROLE public_reader;
\echo --- as public_reader, tenant policy scoped TO app_rw (FIXED: only the public row)
SELECT visibility, v FROM facts ORDER BY id;
COMMIT;
