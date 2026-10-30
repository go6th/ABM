-- Migration 0093 (leakage review 2026-10-30, fix wave D2): F1 exclusions reach the web tables, F2
-- repeats across runs, F3 tenant deletion reaches the rest of the client's data.
--
-- 1. F1. view_materializations.exclusion_hash records which tenant exclusion set the run's web
--    tables were filtered with (readers.exclusions.exclusion_hash; NULL: materialised before the
--    column existed). A read after the tenant changed its exclusions finds a stale
--    materialisation and builds it again. view_rows and view_materializations are DERIVED and
--    recomputable but insert-only for the application role; reset_view_run() (a definer
--    function, below) is the one narrow way to replace one run's rows.
-- 2. F2. Indexes for "the newest row of this fact, posting, person or technology of the account":
--    every account-wide reader keeps one row per equal thing across the runs of an account.
-- 3. F3. The deletion plan (config/deletion.yaml) now covers the client data of the research
--    pipeline, the uploads, the chat and the derived web tables: the eraser role is granted DELETE
--    on exactly the tables of the plan, and erase_rows (definer rights, owner tenant_eraser)
--    carries them in its hard-coded allow-list. A test ties the plan, this list and the database
--    together (tests/system/test_erasure_classification.py).
-- Rollback: 0093_leakage_fix_wave_d2.down.sql.

ALTER TABLE view_materializations ADD COLUMN exclusion_hash text;
ALTER TABLE view_materializations ADD CONSTRAINT ck_view_materializations_exclusions
   CHECK (exclusion_hash IS NULL OR exclusion_hash ~ '^([0-9a-f]{16}|none)$');
COMMENT ON COLUMN view_materializations.exclusion_hash IS 'The tenant exclusion set the run''s web tables were filtered with; NULL before migration 0093. A different current set means the run is materialised again.';

CREATE INDEX ix_facts_account_fingerprint ON facts (account_id, fingerprint, created_at DESC);
CREATE INDEX ix_job_postings_account_key ON job_postings (account_id, posting_key, created_at DESC);
CREATE INDEX ix_people_account_person_key ON people (account_id, person_key, created_at DESC)
   WHERE person_key IS NOT NULL;
CREATE INDEX ix_tech_detections_account_technology ON tech_detections (account_id, technology, created_at DESC);

-- reset_view_run(): deletes the web-table rows and the materialisation record of ONE run of the
-- CALLER's context tenant, so the run can be materialised again under the tenant's current
-- exclusions. It never takes a tenant, touches no other table and returns the number of rows
-- removed from view_rows. Both tables are derived from facts, people and postings and can always
-- be rebuilt.
-- reviewed: SECURITY DEFINER, owner tenant_eraser (NOLOGIN, NOBYPASSRLS, member of app_rw so the tenant and owner policies apply).
CREATE FUNCTION reset_view_run(p_run uuid) RETURNS integer
   LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
   n integer;
BEGIN
   IF app_tenant() IS NULL THEN
      RAISE EXCEPTION 'reset_view_run: no tenant context' USING ERRCODE = 'insufficient_privilege';
   END IF;
   IF p_run IS NULL THEN
      RAISE EXCEPTION 'reset_view_run: no run' USING ERRCODE = 'check_violation';
   END IF;
   DELETE FROM view_rows WHERE tenant_id = app_tenant() AND run_id = p_run;
   GET DIAGNOSTICS n = ROW_COUNT;
   DELETE FROM view_materializations WHERE tenant_id = app_tenant() AND run_id = p_run;
   RETURN n;
END $$;
ALTER FUNCTION reset_view_run(uuid) OWNER TO tenant_eraser;
REVOKE ALL ON FUNCTION reset_view_run(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION reset_view_run(uuid) TO app_rw;

GRANT DELETE ON
   fact_evidence, grade_events, verifier_findings, claim_drops, report_sections, deliverables,
   job_posting_platform_sentences, job_posting_classifications, job_posting_observations,
   job_postings, tech_detections, tech_stack_items, link_archive, chunks, abm_distill_outputs,
   abm_sense_checks, alert_feedback, alert_outbox, alerts, chat_turns, chat_threads,
   connector_access_log, connector_grants, connector_quarantine, connector_items,
   connector_oauth_states, connector_connections, coverage_row_events, coverage_rows,
   crm_write_events, crm_writes, crm_mappings, digest_runs, document_deletions, document_parts,
   document_passages, document_redactions, document_table_rows, document_tables, download_jobs,
   export_verifications, export_packages, fact_reevaluations, intake_drops,
   internal_deliverables, job_pipeline_passes, job_pipeline_runs, lead_verification_queries,
   lead_verifications, leak_checks, link_checks, missing_observations, monitor_runs,
   monitor_configs, notification_deliveries, notification_outbox, notifications,
   people_pipeline_runs, person_events, person_post_pulls, person_search_pages, prepulled_files,
   prepulled_intent_events, prepulled_intents, pull_manifests, raw_files, refresh_row_plans,
   research_file_chunks, research_file_deletions, research_file_findings,
   research_file_model_calls, research_file_state, research_file_upload_parts,
   research_file_uploads, research_files, research_jobs, review_samples, run_coverage_meta,
   run_documents, documents, run_feedback, run_gate_results, saved_views, signal_event_evidence,
   signal_event_links, signal_history, view_materializations, view_rows, work_unit_events,
   work_units, model_reply_log, auth_sessions, login_states, saml_assertion_ids,
   tenant_model_credentials, idp_connections, branding_assets, alert_suppressions,
   signal_events, facts, delta_items, deltas, refresh_runs, refresh_slots, checkpoint_writes,
   checkpoint_blobs, checkpoints
   TO tenant_eraser;

-- erase_rows(): deletes at most p_limit rows of the CALLER's context tenant (app_rw's tenant
-- policies, inherited) from one table of the hard-coded allow-list, only while deletion request
-- p_request has a dry run, was approved by a person other than its requester, and is neither
-- completed nor cancelled. Returns the number of rows deleted. Unchanged from migration 0091
-- except for the allow-list, which now holds every table of config/deletion.yaml `erase`.
-- reviewed: SECURITY DEFINER, owner tenant_eraser (NOLOGIN, NOBYPASSRLS); allow-listed tables, approved open requests only.
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
   IF p_table NOT IN (
         'fact_evidence', 'grade_events', 'verifier_findings', 'claim_drops',
         'report_sections', 'deliverables', 'job_posting_platform_sentences',
         'job_posting_classifications', 'job_posting_observations', 'job_postings',
         'tech_detections', 'tech_stack_items', 'link_archive', 'chunks',
         'abm_distill_outputs', 'abm_sense_checks', 'alert_feedback', 'alert_outbox', 'alerts',
         'chat_turns', 'chat_threads', 'connector_access_log', 'connector_grants',
         'connector_quarantine', 'connector_items', 'connector_oauth_states',
         'connector_connections', 'coverage_row_events', 'coverage_rows', 'crm_write_events',
         'crm_writes', 'crm_mappings', 'digest_runs', 'document_deletions', 'document_parts',
         'document_passages', 'document_redactions', 'document_table_rows', 'document_tables',
         'download_jobs', 'export_verifications', 'export_packages', 'fact_reevaluations',
         'intake_drops', 'internal_deliverables', 'job_pipeline_passes', 'job_pipeline_runs',
         'lead_verification_queries', 'lead_verifications', 'leak_checks', 'link_checks',
         'missing_observations', 'monitor_runs', 'monitor_configs', 'notification_deliveries',
         'notification_outbox', 'notifications', 'people_pipeline_runs', 'person_events',
         'person_post_pulls', 'person_search_pages', 'prepulled_files',
         'prepulled_intent_events', 'prepulled_intents', 'pull_manifests', 'raw_files',
         'refresh_row_plans', 'research_file_chunks', 'research_file_deletions',
         'research_file_findings', 'research_file_model_calls', 'research_file_state',
         'research_file_upload_parts', 'research_file_uploads', 'research_files',
         'research_jobs', 'review_samples', 'run_coverage_meta', 'run_documents', 'documents',
         'run_feedback', 'run_gate_results', 'saved_views', 'signal_event_evidence',
         'signal_event_links', 'signal_history', 'view_materializations', 'view_rows',
         'work_unit_events', 'work_units', 'model_reply_log', 'auth_sessions', 'login_states',
         'saml_assertion_ids', 'tenant_model_credentials', 'idp_connections',
         'branding_assets', 'alert_suppressions', 'signal_events', 'facts', 'delta_items',
         'deltas', 'refresh_runs', 'refresh_slots', 'checkpoint_writes', 'checkpoint_blobs',
         'checkpoints') THEN
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
