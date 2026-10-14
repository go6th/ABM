-- Remove sign-in discovery by domain.
DROP FUNCTION IF EXISTS idp_connection_for_domain(text);
REVOKE SELECT (verified_domains) ON idp_connections FROM identity_resolver;
