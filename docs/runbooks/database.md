# Runbook: database

- Instance: Cloud SQL PostgreSQL 16, private IP only, IAM database authentication, automated
  backups with point-in-time recovery (infra/modules/database).
- Migrations run as the migrator role, never as the runtime: open a Cloud SQL Auth Proxy session
  from an operator machine and run
  `MIGRATOR_DATABASE_URL=... APP_RW_PASSWORD=... alembic upgrade head`. Migrations are expand-only;
  every file has a `.down.sql`.
- After migrating, the RLS catalog check must return no rows:
  `psql "$MIGRATOR_DATABASE_URL" -tA -f .claude/skills/account-intelligence-platform/scripts/check_rls_catalog.sql`.
- Grant the runtime's IAM database user the `app_rw` role (once):
  `GRANT app_rw TO "RUNTIME_SA@PROJECT.iam";` (VERIFY(V-SQL-1) on the real instance).
- Restore: point-in-time recovery to a new instance, verify the RLS catalog, switch the
  `CLOUD_SQL_INSTANCE` setting, redeploy. Practise it on staging before relying on it.
