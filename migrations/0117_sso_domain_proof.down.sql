-- Undo migration 0117: discovery uses every active connection's listed domains again (0062).
-- domains_proven_at stays (no column is dropped); nothing reads it after this.
DROP FUNCTION idp_domain_claimed_elsewhere(text);
CREATE OR REPLACE FUNCTION idp_connection_for_domain(p_domain text)
   RETURNS text
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT c.slug FROM idp_connections c
    WHERE c.status = 'active' AND lower(p_domain) = ANY (c.verified_domains)
    ORDER BY c.slug
    LIMIT 1
$$;
ALTER FUNCTION idp_connection_for_domain(text) OWNER TO identity_resolver;
REVOKE ALL ON FUNCTION idp_connection_for_domain(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION idp_connection_for_domain(text) TO app_rw;
REVOKE SELECT (domains_proven_at) ON idp_connections FROM identity_resolver;
