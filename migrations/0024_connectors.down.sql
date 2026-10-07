-- Rollback of 0024_connectors.sql (development only; production never contracts in the same release).
DROP TABLE IF EXISTS connector_access_log;
DROP TABLE IF EXISTS connector_items;
DROP TABLE IF EXISTS connector_quarantine;
DROP TABLE IF EXISTS connector_grants;
DROP TABLE IF EXISTS connector_oauth_states;
DROP TABLE IF EXISTS connector_connections;
