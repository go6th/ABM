-- Rollback of 0005_sources.sql: drops what it adds, in reverse order.
DROP TABLE IF EXISTS sources;
DROP FUNCTION IF EXISTS sources_guard_identity();
