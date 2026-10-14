-- Sign-in discovery by work-email domain (POST /v1/sso/discover). identity_resolver gains read of
-- the verified_domains column only; the function returns one slug and nothing else.
GRANT SELECT (verified_domains) ON idp_connections TO identity_resolver;

-- reviewed: SECURITY DEFINER (owner identity_resolver, NOLOGIN, NOBYPASSRLS, column grants
-- only). Returns the slug of one ACTIVE connection whose verified domains hold p_domain, or NULL;
-- no tenant id, no connection id, nothing about the company.
CREATE FUNCTION idp_connection_for_domain(p_domain text)
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
