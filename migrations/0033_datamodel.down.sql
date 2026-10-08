-- Rollback of 0033_datamodel.sql (development only; production never contracts in the same release).
DROP TABLE IF EXISTS export_verifications;
ALTER TABLE tech_detections DROP CONSTRAINT IF EXISTS ck_tech_detections_cross_check;
ALTER TABLE tech_detections DROP COLUMN IF EXISTS cross_check_result;
ALTER TABLE facts DROP CONSTRAINT IF EXISTS ck_facts_hypothesis_has_question;
DROP TRIGGER IF EXISTS account_runs_guard_pins ON account_runs;
DROP FUNCTION IF EXISTS account_runs_guard_pins();
ALTER TABLE account_runs DROP CONSTRAINT IF EXISTS ck_account_runs_model_versions;
ALTER TABLE account_runs DROP COLUMN IF EXISTS model_versions, DROP COLUMN IF EXISTS checklist_version;
ALTER TABLE accounts DROP CONSTRAINT IF EXISTS ck_accounts_careers_url_visited;
ALTER TABLE accounts DROP COLUMN IF EXISTS careers_fetch_ref;
