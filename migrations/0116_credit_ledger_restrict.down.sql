-- Undo migration 0116: the credit ledger cascades from research_jobs again, research_jobs is back
-- on the erase_rows allow-list with the eraser's DELETE, and scrub_rows() is gone. No row changes.
DROP FUNCTION scrub_rows(uuid, text, integer);
GRANT DELETE ON research_jobs TO tenant_eraser;

-- erase_rows(): as in 0106.
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
         'tech_detections', 'tech_stack_items', 'platform_teams', 'news_events', 'person_comments', 'org_connections', 'research_notes', 'pull_log_calls',
         'link_archive', 'chunks',
         'abm_distill_outputs', 'abm_sense_checks', 'alert_feedback', 'alert_outbox', 'alerts',
         'chat_turns', 'chat_threads', 'connector_access_log', 'connector_grants',
         'connector_quarantine', 'connector_items', 'connector_oauth_states',
         'connector_connections', 'coverage_row_events', 'coverage_rows', 'crm_write_events',
         'crm_writes', 'crm_mappings', 'digest_runs', 'document_deletions', 'document_parts',
         'document_passages', 'document_redactions', 'document_table_rows', 'document_tables',
         'document_members', 'import_run_parts', 'import_runs',
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

ALTER TABLE credit_entries DROP CONSTRAINT fk_credit_entries_job;
ALTER TABLE credit_entries
   ADD CONSTRAINT fk_credit_entries_job FOREIGN KEY (tenant_id, job_id)
      REFERENCES research_jobs (tenant_id, id) ON DELETE CASCADE;
