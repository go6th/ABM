-- Remove the two research input modes: document tables, gate evidence and the added columns.

DROP FUNCTION IF EXISTS documents_orphaned(timestamptz);
DROP TABLE IF EXISTS internal_deliverables;
DROP TABLE IF EXISTS leak_checks;
DROP TABLE IF EXISTS run_gate_results;
DROP TABLE IF EXISTS document_deletions;
DROP TABLE IF EXISTS fact_reevaluations;
ALTER TABLE fact_evidence DROP CONSTRAINT IF EXISTS ck_fact_evidence_document;
ALTER TABLE fact_evidence DROP CONSTRAINT IF EXISTS ck_fact_evidence_traceable;
ALTER TABLE fact_evidence ADD CONSTRAINT ck_fact_evidence_traceable
   CHECK (evidence_kind = 'search_proof'
      OR (url IS NOT NULL AND span_sha256 IS NOT NULL AND source_tier IS NOT NULL
          AND evidence_date IS NOT NULL));
ALTER TABLE fact_evidence DROP CONSTRAINT IF EXISTS ck_fact_evidence_kind;
ALTER TABLE fact_evidence ADD CONSTRAINT ck_fact_evidence_kind
   CHECK (evidence_kind IN ('source', 'inherited', 'search_proof'));
ALTER TABLE fact_evidence DROP COLUMN IF EXISTS document_location;
ALTER TABLE fact_evidence DROP COLUMN IF EXISTS document_id;
ALTER TABLE chunks DROP COLUMN IF EXISTS embedding_model;
ALTER TABLE chunks DROP COLUMN IF EXISTS heading_path;
ALTER TABLE chunks DROP COLUMN IF EXISTS location;
ALTER TABLE chunks DROP COLUMN IF EXISTS passage_seq;
DROP TABLE IF EXISTS run_documents;
DROP TABLE IF EXISTS document_redactions;
DROP TABLE IF EXISTS document_passages;
DROP TABLE IF EXISTS documents;
DROP TABLE IF EXISTS document_policies;
ALTER TABLE account_runs DROP CONSTRAINT IF EXISTS ck_account_runs_input_mode;
ALTER TABLE account_runs DROP COLUMN IF EXISTS input_mode;
