-- Migration 0092 (security review 2026-10-29: F1, F2, F12, F13): the link archive reaches
-- erasure, keeps its index fields beside the row and lets the newest status win.
--
-- 1. link_archive.meta (jsonb, at most 2 KiB, NULL for rows written before this migration): the
--    few fields the archive index shows for a copy (status when captured, location, requisition,
--    the record's schema). The index page reads them from the row instead of opening up to 200
--    stored bodies per request. No backfill: a row without it lists with those fields blank and
--    the page reads at most a few old bodies (archive.LEGACY_BODY_READS).
-- 2. archived_at may move FORWARD, nothing else may change. The copy is content-addressed, so a
--    page that returns to earlier content (a posting open, then closed, then open again with
--    the same text) maps to the OLD key; the insert was skipped and the newest row stayed the
--    closed copy. The writer now inserts with ON CONFLICT DO UPDATE SET archived_at = the later
--    time. app_rw gets UPDATE on that one column and a trigger refuses any other change and any
--    move backwards. The table is an index over copies, not a ledger: no fact, grade or money
--    row depends on archived_at.
-- 3. erase_archive_urls(): the erasure of ONE person reaches the archive. See the function.

ALTER TABLE link_archive ADD COLUMN meta jsonb;
ALTER TABLE link_archive ADD CONSTRAINT ck_link_archive_meta CHECK (
   meta IS NULL OR (jsonb_typeof(meta) = 'object' AND octet_length(meta::text) <= 2048));
COMMENT ON COLUMN link_archive.meta IS 'Index fields of the copy (status_at_capture, location, requisition, schema); NULL for rows written before migration 0092.';

GRANT UPDATE (archived_at) ON link_archive TO app_rw;
CREATE FUNCTION link_archive_touch_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
   IF (to_jsonb(NEW) - 'archived_at') IS DISTINCT FROM (to_jsonb(OLD) - 'archived_at') THEN
      RAISE EXCEPTION 'link_archive: only archived_at may change'
         USING ERRCODE = 'insufficient_privilege';
   END IF;
   IF NEW.archived_at < OLD.archived_at THEN
      RAISE EXCEPTION 'link_archive: archived_at only moves forward'
         USING ERRCODE = 'check_violation';
   END IF;
   RETURN NEW;
END $$;
CREATE TRIGGER trg_link_archive_touch_only BEFORE UPDATE ON link_archive
   FOR EACH ROW EXECUTE FUNCTION link_archive_touch_only();

-- erase_archive_urls(): it never takes a tenant: it deletes only rows of the CALLER's context
-- tenant, of the one account passed, and only while that account holds a person already erased
-- (tombstone on people or person_profiles), so it cannot be used to empty an archive. It deletes
-- (a) rows whose url_hash is in the passed array (at most 500: the erased person's profile
-- addresses) and (b) rows of kind 'profile' whose title names the passed name (search results
-- titled "Name - Role - Company | LinkedIn"). It returns the number of rows deleted. The stored
-- bodies are NOT deleted here: the platform payload store has no delete operation
-- (config/deletion.yaml raw_payload_objects); with the index row gone no route can reach a body,
-- and profile copies are never served anyway.
-- reviewed: SECURITY DEFINER, owner tenant_eraser (NOLOGIN, NOBYPASSRLS, member of app_rw so the
-- tenant and owner policies of link_archive apply to it); tenant from app_tenant() only.
CREATE FUNCTION erase_archive_urls(p_account uuid, p_hashes text[], p_name text DEFAULT NULL)
   RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
   n integer;
   wanted text := lower(btrim(coalesce(p_name, '')));
BEGIN
   IF app_tenant() IS NULL THEN
      RAISE EXCEPTION 'erase_archive_urls: no tenant context' USING ERRCODE = 'insufficient_privilege';
   END IF;
   IF p_account IS NULL THEN
      RAISE EXCEPTION 'erase_archive_urls: no account' USING ERRCODE = 'check_violation';
   END IF;
   IF coalesce(cardinality(p_hashes), 0) > 500 THEN
      RAISE EXCEPTION 'erase_archive_urls: too many addresses' USING ERRCODE = 'check_violation';
   END IF;
   IF NOT EXISTS (SELECT 1 FROM people WHERE account_id = p_account AND erased_at IS NOT NULL)
      AND NOT EXISTS (SELECT 1 FROM person_profiles
                       WHERE account_id = p_account AND erased_at IS NOT NULL) THEN
      RAISE EXCEPTION 'erase_archive_urls: no erased person in this account'
         USING ERRCODE = 'insufficient_privilege';
   END IF;
   -- a name of fewer than 3 characters would match too much to mean one person
   IF char_length(wanted) < 3 THEN
      wanted := '';
   END IF;
   DELETE FROM link_archive
    WHERE tenant_id = app_tenant() AND account_id = p_account
      AND (url_hash = ANY(coalesce(p_hashes, ARRAY[]::text[]))
           OR (kind = 'profile' AND wanted <> ''
               AND position(wanted in lower(coalesce(title, ''))) > 0));
   GET DIAGNOSTICS n = ROW_COUNT;
   RETURN n;
END $$;
ALTER FUNCTION erase_archive_urls(uuid, text[], text) OWNER TO tenant_eraser;
REVOKE ALL ON FUNCTION erase_archive_urls(uuid, text[], text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION erase_archive_urls(uuid, text[], text) TO app_rw;
