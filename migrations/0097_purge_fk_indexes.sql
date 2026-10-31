-- Migration 0097 (2026-10-31): index the foreign keys that point at rows the account purge deletes.
--
-- Deleting a parent row makes the database look for child rows that still point at it. These
-- eighteen foreign keys had no index, so every parent row deleted meant a scan of the whole child
-- table, dead rows of the same transaction included. One account purge (migration 0096) deletes
-- tens of thousands of facts, postings and queries in one transaction and ran for over 45 minutes
-- on staging (2026-10-31). With the indexes each lookup is one index probe.
-- Rollback: 0097_purge_fk_indexes.down.sql (drops the indexes; nothing else changes).

CREATE INDEX IF NOT EXISTS ix_facts_superseded_by ON facts (tenant_id, superseded_by);
CREATE INDEX IF NOT EXISTS ix_grade_events_fact_id ON grade_events (tenant_id, fact_id);
CREATE INDEX IF NOT EXISTS ix_fact_reevaluations_fact_id ON fact_reevaluations (tenant_id, fact_id);
CREATE INDEX IF NOT EXISTS ix_signal_events_fact_id ON signal_events (fact_id);
CREATE INDEX IF NOT EXISTS ix_job_posting_classifications_run_id_posting_key ON job_posting_classifications (tenant_id, run_id, posting_key);
CREATE INDEX IF NOT EXISTS ix_job_posting_platform_sentences_run_id_posting_key ON job_posting_platform_sentences (tenant_id, run_id, posting_key);
CREATE INDEX IF NOT EXISTS ix_lead_verification_queries_lead_verification_id ON lead_verification_queries (tenant_id, lead_verification_id);
CREATE INDEX IF NOT EXISTS ix_run_documents_document_id ON run_documents (tenant_id, document_id);
CREATE INDEX IF NOT EXISTS ix_raw_files_first_document_id ON raw_files (tenant_id, first_document_id);
CREATE INDEX IF NOT EXISTS ix_deliverables_supersedes ON deliverables (tenant_id, supersedes);
CREATE INDEX IF NOT EXISTS ix_alert_feedback_alert_id ON alert_feedback (alert_id);
CREATE INDEX IF NOT EXISTS ix_signal_events_delta_item_id ON signal_events (delta_item_id);
CREATE INDEX IF NOT EXISTS ix_alerts_fact_delta_id ON alerts (fact_delta_id);
CREATE INDEX IF NOT EXISTS ix_alerts_event_id ON alerts (event_id);
CREATE INDEX IF NOT EXISTS ix_alert_suppressions_event_id ON alert_suppressions (event_id);
CREATE INDEX IF NOT EXISTS ix_export_verifications_package_id ON export_verifications (package_id);
CREATE INDEX IF NOT EXISTS ix_signal_history_expired_by_run ON signal_history (expired_by_run);
CREATE INDEX IF NOT EXISTS ix_signal_event_links_event_b ON signal_event_links (event_b);
