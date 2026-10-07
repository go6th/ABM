-- Rollback of 0022_audit.sql (development only; production never contracts in the same release).
DROP TABLE IF EXISTS audit_chain_checks;
DROP TABLE IF EXISTS audit_events;
