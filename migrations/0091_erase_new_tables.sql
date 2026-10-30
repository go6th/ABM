-- Migration 0091: tenant deletion also erases the link archive and the organisation tech stack
-- (tables added by migrations 0086 and 0090 after the erasure allow-list of migration 0020).
-- Both hold the client's own research output (copies of pages read, the technologies named), so a
-- deletion on request removes their ROWS with the other derived data. The archived bodies in the
-- payload store are NOT deleted: the store has no delete operation (config/deletion.yaml,
-- raw_payload_objects); without the index rows no route reaches them (security review
-- 2026-10-29, F2). Same rules as 0020: the eraser
-- role is granted DELETE on exactly these tables, and erase_rows (a definer-rights function owned by
-- tenant_eraser, deletes at most p_limit rows of the caller's context tenant while an approved
-- deletion request is open) gains them in its hard-coded allow-list. config/deletion.yaml lists
-- them in `erase` (a test ties the two lists together). Rollback: 0091_erase_new_tables.down.sql.

GRANT DELETE ON link_archive, tech_stack_items TO tenant_eraser;

-- reviewed: SECURITY DEFINER (owner tenant_eraser, NOLOGIN, NOBYPASSRLS). Deletes at most
-- p_limit rows of the CALLER's context tenant (app_rw's tenant policies, inherited) from one table
-- of the hard-coded allow-list, only while deletion request p_request has a dry run, was approved
-- by a person other than its requester, and is neither completed nor cancelled. Returns the
-- number of rows deleted. Unchanged from migration 0020 except for the allow-list.
CREATE OR REPLACE FUNCTION erase_rows(p_request uuid, p_table text, p_limit integer)
   RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
   n integer;
BEGIN
   IF app_tenant() IS NULL THEN
      RAISE EXCEPTION 'erase_rows: no tenant context' USING ERRCODE = 'insufficient_privilege';
   END IF;
   IF p_limit IS NULL OR p_limit < 1 OR p_limit > 5000 THEN
      RAISE EXCEPTION 'erase_rows: batch size out of range' USING ERRCODE = 'check_violation';
   END IF;
   IF p_table NOT IN ('fact_evidence', 'grade_events', 'facts', 'claim_drops', 'report_sections',
         'deliverables', 'job_posting_platform_sentences', 'job_posting_classifications',
         'job_posting_observations', 'job_postings', 'tech_detections', 'chunks',
         'verifier_findings', 'link_archive', 'tech_stack_items') THEN
      RAISE EXCEPTION 'erase_rows: % is not erasable', p_table
         USING ERRCODE = 'insufficient_privilege';
   END IF;
   IF NOT EXISTS (SELECT 1 FROM tenant_events d WHERE d.event = 'deletion_dry_run'
                   AND d.detail_json ->> 'request_id' = p_request::text)
      OR NOT EXISTS (SELECT 1 FROM tenant_events a JOIN tenant_events r
                   ON r.event = 'deletion_requested'
                  AND r.detail_json ->> 'request_id' = p_request::text
                WHERE a.event = 'deletion_approved'
                  AND a.detail_json ->> 'request_id' = p_request::text
                  AND a.actor <> r.actor)
      OR EXISTS (SELECT 1 FROM tenant_events e
                  WHERE e.event IN ('deletion_completed', 'deletion_cancelled')
                    AND e.detail_json ->> 'request_id' = p_request::text) THEN
      RAISE EXCEPTION 'erase_rows: request % is not approved and open', p_request
         USING ERRCODE = 'insufficient_privilege';
   END IF;
   EXECUTE format('DELETE FROM %I WHERE ctid IN (SELECT ctid FROM %I LIMIT %s)',
                  p_table, p_table, p_limit);
   GET DIAGNOSTICS n = ROW_COUNT;
   RETURN n;
END $$;
ALTER FUNCTION erase_rows(uuid, text, integer) OWNER TO tenant_eraser;
REVOKE ALL ON FUNCTION erase_rows(uuid, text, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION erase_rows(uuid, text, integer) TO app_rw;
