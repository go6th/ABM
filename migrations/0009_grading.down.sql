DROP TABLE IF EXISTS lead_verification_queries;
DROP TABLE IF EXISTS lead_verifications;
DROP TABLE IF EXISTS claim_drops;
DROP TABLE IF EXISTS grade_events;
DROP TRIGGER IF EXISTS facts_require_evidence ON facts;
DROP FUNCTION IF EXISTS facts_require_evidence();
DROP TABLE IF EXISTS fact_evidence;
DROP TRIGGER IF EXISTS facts_guard_append_only ON facts;
DROP FUNCTION IF EXISTS facts_guard_append_only();
DROP INDEX IF EXISTS uq_facts_tenant_id_write_key;
DROP INDEX IF EXISTS ix_facts_run_id;
ALTER TABLE facts
   DROP CONSTRAINT IF EXISTS ck_facts_product_grade_present,
   DROP CONSTRAINT IF EXISTS ck_facts_confirmed_needs_primary_read,
   DROP CONSTRAINT IF EXISTS ck_facts_only_confirmed_may_be_stated,
   DROP CONSTRAINT IF EXISTS fk_facts_tenant_id_superseded_by_facts,
   DROP COLUMN IF EXISTS run_id, DROP COLUMN IF EXISTS claim_hash, DROP COLUMN IF EXISTS write_key,
   DROP COLUMN IF EXISTS claim_kind, DROP COLUMN IF EXISTS value_text, DROP COLUMN IF EXISTS confirming_question,
   DROP COLUMN IF EXISTS reasoning, DROP COLUMN IF EXISTS flags, DROP COLUMN IF EXISTS precedence_rule,
   DROP COLUMN IF EXISTS config_hash, DROP COLUMN IF EXISTS schema_version, DROP COLUMN IF EXISTS lead_status,
   DROP COLUMN IF EXISTS may_state_to_prospect, DROP COLUMN IF EXISTS superseded_by;
ALTER TABLE facts DROP CONSTRAINT IF EXISTS uq_facts_tenant_id_id;
