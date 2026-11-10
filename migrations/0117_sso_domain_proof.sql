-- SD-10 (system design review 2026-11-10): sign-in discovery and JIT trust only proven domains.
-- idp_connections.domains_proven_at is set when a save proved every listed domain by its DNS TXT
-- record (identity.domain_proof); a connection saved before this migration has none, so its
-- domains are no longer used for discovery or JIT until an admin saves it again with the
-- records published. No row is changed here.
ALTER TABLE idp_connections ADD COLUMN IF NOT EXISTS domains_proven_at timestamptz;
GRANT SELECT (domains_proven_at) ON idp_connections TO identity_resolver;

-- reviewed: SECURITY DEFINER (owner identity_resolver, NOLOGIN, NOBYPASSRLS, column grants
-- only). The slug of the one ACTIVE, PROVEN connection whose domains hold p_domain; NULL when
-- none, or when more than one tenant holds it (ambiguous: the user signs in with a password).
-- Returns no tenant id, no connection id, nothing about the company.
CREATE OR REPLACE FUNCTION idp_connection_for_domain(p_domain text)
   RETURNS text
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT min(c.slug) FROM idp_connections c
    WHERE c.status = 'active' AND c.domains_proven_at IS NOT NULL
      AND lower(p_domain) = ANY (c.verified_domains)
   HAVING count(DISTINCT c.tenant_id) = 1
$$;
ALTER FUNCTION idp_connection_for_domain(text) OWNER TO identity_resolver;
REVOKE ALL ON FUNCTION idp_connection_for_domain(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION idp_connection_for_domain(text) TO app_rw;

-- reviewed: SECURITY DEFINER (owner identity_resolver, NOLOGIN, NOBYPASSRLS, column grants
-- only). True when an ACTIVE, PROVEN connection of ANOTHER tenant than the context one holds
-- p_domain. A boolean only: which tenant holds it is never returned.
CREATE FUNCTION idp_domain_claimed_elsewhere(p_domain text)
   RETURNS boolean
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT EXISTS (SELECT 1 FROM idp_connections c
                   WHERE c.status = 'active' AND c.domains_proven_at IS NOT NULL
                     AND lower(p_domain) = ANY (c.verified_domains)
                     AND c.tenant_id IS DISTINCT FROM app_tenant())
$$;
ALTER FUNCTION idp_domain_claimed_elsewhere(text) OWNER TO identity_resolver;
REVOKE ALL ON FUNCTION idp_domain_claimed_elsewhere(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION idp_domain_claimed_elsewhere(text) TO app_rw;
