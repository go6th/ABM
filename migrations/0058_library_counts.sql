-- The 6th Library's operator view: how many clients each library account is published to. The
-- publications are tenant rows, so the count is read through an identifier-and-count-only
-- lookup instead of reading any tenant's rows.

-- reviewed: SECURITY DEFINER returns (library_account_id, count) pairs only: no tenant id, no
-- tenant content; search_path pinned; EXECUTE granted to app_rw only.
CREATE FUNCTION library_publication_counts()
   RETURNS TABLE (library_account_id uuid, clients bigint)
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT p.library_account_id, count(*) FROM library_publications p
    WHERE p.revoked_at IS NULL GROUP BY p.library_account_id
$$;
REVOKE ALL ON FUNCTION library_publication_counts() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION library_publication_counts() TO app_rw;
