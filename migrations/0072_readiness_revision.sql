-- Readiness reads the schema revision (api/routes/health.py: "migrations" check): the runtime
-- role may read the one-row alembic_version table, nothing else of it (no tenant data).
-- Rollback: 0072_readiness_revision.down.sql.
GRANT SELECT ON alembic_version TO app_rw;
