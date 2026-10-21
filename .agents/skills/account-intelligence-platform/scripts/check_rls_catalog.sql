-- check_rls_catalog.sql: run against a MIGRATED database (CI step after `alembic upgrade head`).
-- Returns one row per violation; an empty result is a pass. This is the backstop for what the regex lint
-- cannot see (tables created or secured in DO loops, library-owned tables).
--   psql "$ADMIN_DSN" -v ON_ERROR_STOP=1 -tA -f scripts/check_rls_catalog.sql | tee /dev/stderr | (! grep .)

-- 1. every table with a tenant_id column has RLS enabled and forced (partition children excluded: app_rw has
--    no grant on them, which check 3 verifies)
SELECT 'no forced RLS: ' || c.relname
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition
  AND NOT (c.relrowsecurity AND c.relforcerowsecurity)
UNION ALL
-- 2. library-owned tables keyed by thread_id are covered too
SELECT 'no forced RLS: ' || c.relname
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'thread_id' AND NOT a.attisdropped
WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT (c.relrowsecurity AND c.relforcerowsecurity)
UNION ALL
-- 3. the runtime role must not own anything, must not bypass RLS, and must have no grant on partition children
SELECT 'app_rw is superuser or BYPASSRLS' FROM pg_roles WHERE rolname = 'app_rw' AND (rolsuper OR rolbypassrls)
UNION ALL
SELECT 'app_rw owns ' || c.relname FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner
WHERE r.rolname = 'app_rw' AND c.relkind IN ('r', 'p', 'v', 'm')
UNION ALL
SELECT 'app_rw has direct grant on partition ' || c.relname
FROM pg_class c WHERE c.relispartition AND has_table_privilege('app_rw', c.oid, 'SELECT,INSERT,UPDATE,DELETE')
UNION ALL
-- 4. policies with no TO clause on tables that have a role-specific policy (permissive policies are OR-ed)
SELECT 'unscoped policy ' || p.polname || ' on ' || c.relname
FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
WHERE p.polroles = '{0}'
  AND EXISTS (SELECT 1 FROM pg_policy q WHERE q.polrelid = p.polrelid AND q.polroles <> '{0}')
UNION ALL
-- 5. public_reader must have no grant on chunks
SELECT 'public_reader can read chunks'
WHERE has_table_privilege('public_reader', 'chunks', 'SELECT')
UNION ALL
-- 6. tenant-owned tables must have NOT NULL tenant_id.
SELECT 'nullable tenant_id: ' || c.relname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition
  AND NOT a.attnotnull
UNION ALL
-- 7. Every tenant-owned table must have an app_rw-only policy with both predicates.
SELECT 'missing app_rw USING/WITH CHECK policy: ' || c.relname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition
  AND NOT EXISTS (
      SELECT 1
      FROM pg_policy p
      WHERE p.polrelid = c.oid
        AND p.polroles = ARRAY[(SELECT oid FROM pg_roles WHERE rolname = 'app_rw')]
        AND p.polqual IS NOT NULL
        AND p.polwithcheck IS NOT NULL
  )
UNION ALL
-- 8. App runtime must not have DELETE on append-only or retained records.
SELECT 'app_rw can delete append-only table ' || t.table_name
FROM unnest(ARRAY[
    'sources', 'fact_evidence', 'people', 'raw_payloads', 'tool_call_intents',
    'wallet_ledger', 'tool_calls'
]) AS t(table_name)
WHERE to_regclass(t.table_name) IS NOT NULL
  AND has_table_privilege('app_rw', t.table_name, 'DELETE');
