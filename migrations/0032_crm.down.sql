-- Rollback of 0032_crm.sql (development only; production never contracts in the same release).
DROP TABLE IF EXISTS crm_write_events;
DROP TABLE IF EXISTS crm_writes;
DROP TABLE IF EXISTS crm_mappings;
DROP TABLE IF EXISTS crm_connection_events;
