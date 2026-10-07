-- Rollback of 0021_identity.sql (development only; production never contracts in the same release).
DROP FUNCTION IF EXISTS session_tenant(text);
DROP FUNCTION IF EXISTS login_state_tenant(text);
DROP FUNCTION IF EXISTS idp_connection_tenant(text);
DROP TABLE IF EXISTS saml_assertion_ids;
DROP TABLE IF EXISTS auth_events;
DROP TABLE IF EXISTS auth_sessions;
DROP TABLE IF EXISTS login_states;
DROP TABLE IF EXISTS idp_connections;
