-- Rollback of 0020_tenancy.sql (development only; production never contracts in the same release).
DROP FUNCTION IF EXISTS erase_rows(uuid, text, integer);
REVOKE DELETE ON fact_evidence, grade_events, facts, claim_drops, report_sections, deliverables,
   job_posting_platform_sentences, job_posting_classifications, job_posting_observations,
   job_postings, tech_detections, chunks, verifier_findings FROM tenant_eraser;
REVOKE app_rw FROM tenant_eraser;
DROP TABLE IF EXISTS tenant_events;
ALTER TABLE account_runs DROP CONSTRAINT IF EXISTS fk_account_runs_settings_version;
ALTER TABLE account_runs DROP COLUMN IF EXISTS settings_version;
DROP TABLE IF EXISTS tenant_settings_versions;
DROP TABLE IF EXISTS membership_events;
ALTER TABLE memberships DROP CONSTRAINT IF EXISTS memberships_role_check;
ALTER TABLE memberships ADD CONSTRAINT memberships_role_check
   CHECK (role IN ('admin', 'seller', 'viewer'));
ALTER TABLE memberships DROP CONSTRAINT IF EXISTS ck_memberships_version;
ALTER TABLE memberships DROP CONSTRAINT IF EXISTS ck_memberships_status;
ALTER TABLE memberships DROP COLUMN IF EXISTS updated_at;
ALTER TABLE memberships DROP COLUMN IF EXISTS version;
ALTER TABLE memberships DROP COLUMN IF EXISTS status;
DROP TABLE IF EXISTS users;
