-- Rollback of 0004_runs.sql: drops what it adds, in reverse order.
DROP TABLE IF EXISTS runs;
DROP FUNCTION IF EXISTS runs_guard_update();
ALTER TABLE accounts DROP CONSTRAINT IF EXISTS uq_accounts_tenant_id_id;
