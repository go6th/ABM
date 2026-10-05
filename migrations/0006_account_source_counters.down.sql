-- Rollback of 0006_account_source_counters.sql: drops what it adds, in reverse order.
DROP TABLE IF EXISTS account_source_counters;
DROP FUNCTION IF EXISTS account_source_counters_forward_only();
