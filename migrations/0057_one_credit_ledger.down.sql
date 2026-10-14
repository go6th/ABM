-- Reverse 0057_one_credit_ledger: the wallet grants, the lookup, the stop reason and the library kind.
DROP FUNCTION tenants_with_credit_activity(timestamptz);
GRANT INSERT, UPDATE ON wallets TO app_rw;
GRANT INSERT ON wallet_ledger TO app_rw;
ALTER TABLE research_jobs DROP CONSTRAINT ck_research_jobs_stop_reason;
ALTER TABLE research_jobs DROP COLUMN stop_reason;
DROP INDEX uq_research_jobs_run;
DELETE FROM credit_entries WHERE purpose = 'library_add';
DELETE FROM research_jobs WHERE kind = 'library_add';
ALTER TABLE credit_entries DROP CONSTRAINT ck_credit_entries_purpose;
ALTER TABLE credit_entries ADD CONSTRAINT ck_credit_entries_purpose
   CHECK (purpose IN ('new_research', 'web_refresh', 'scheduled_refresh', 'file_analysis'));
ALTER TABLE research_jobs DROP CONSTRAINT ck_research_jobs_scope;
ALTER TABLE research_jobs ADD CONSTRAINT ck_research_jobs_scope
   CHECK (scope IN ('jobs', 'people', 'full', 'file', 'topic'));
ALTER TABLE research_jobs DROP CONSTRAINT ck_research_jobs_kind;
ALTER TABLE research_jobs ADD CONSTRAINT ck_research_jobs_kind
   CHECK (kind IN ('new_research', 'web_refresh', 'scheduled_refresh', 'file_analysis'));
