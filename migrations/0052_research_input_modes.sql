-- Two research input modes (2026-10-13): a run researches a company from public sources
-- (input mode "company") or from the tenant's own uploaded documents (input mode "file").
-- Uploaded material is private: it is stored, scanned, parsed, redacted, chunked, embedded and
-- indexed under the tenant and the account, and facts derived from it are graded Internal and
-- cite the document and the passage. Every table is tenant scoped with forced row level security.

ALTER TABLE account_runs ADD COLUMN input_mode text NOT NULL DEFAULT 'company';
ALTER TABLE account_runs ADD CONSTRAINT ck_account_runs_input_mode
   CHECK (input_mode IN ('company', 'file'));

-- Per-tenant document policy: the redaction tier applied when a document is parsed.
CREATE TABLE document_policies (
   tenant_id       uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   redaction_tier  text NOT NULL DEFAULT 'standard',
   updated_by      text NOT NULL,
   updated_at      timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_document_policies PRIMARY KEY (tenant_id),
   CONSTRAINT ck_document_policies_tier CHECK (redaction_tier IN ('strict', 'standard', 'minimal'))
);
ALTER TABLE document_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_policies FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON document_policies TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE ON document_policies TO app_rw;

-- One uploaded document. account_id is NULL only until the user confirms which company the
-- document is about (a document uploaded from an account page is bound at upload).
CREATE TABLE documents (
   id                uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id         uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   account_id        uuid,
   name              text NOT NULL,
   declared_bytes    bigint NOT NULL,
   size_bytes        bigint,
   declared_type     text NOT NULL,
   detected_type     text,
   sha256            text,
   quarantine_key    text NOT NULL,
   upload_token_sha256 text,
   object_key        text,
   status            text NOT NULL DEFAULT 'awaiting_upload',
   reject_reason     text,
   stop_detail       text,
   redaction_tier    text,
   authoritative     boolean NOT NULL DEFAULT false,
   detected_company  jsonb,
   passages          integer NOT NULL DEFAULT 0,
   redactions        integer NOT NULL DEFAULT 0,
   uploaded_by       text NOT NULL,
   lease_owner       text,
   lease_expires_at  timestamptz,
   upload_expires_at timestamptz NOT NULL,
   created_at        timestamptz NOT NULL DEFAULT now(),
   updated_at        timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_documents PRIMARY KEY (id),
   CONSTRAINT uq_documents_tenant_id UNIQUE (tenant_id, id),
   CONSTRAINT fk_documents_account FOREIGN KEY (tenant_id, account_id)
      REFERENCES accounts (tenant_id, id),
   CONSTRAINT ck_documents_status CHECK (status IN ('awaiting_upload', 'quarantined', 'scanning',
      'rejected', 'parsing', 'parsed', 'indexing', 'indexed', 'failed')),
   CONSTRAINT ck_documents_rejected_reason CHECK ((status = 'rejected') = (reject_reason IS NOT NULL)),
   CONSTRAINT ck_documents_sizes CHECK (declared_bytes >= 0 AND (size_bytes IS NULL OR size_bytes >= 0)),
   CONSTRAINT ck_documents_token CHECK (upload_token_sha256 IS NULL OR upload_token_sha256 ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_documents_sha256 CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_documents_name CHECK (char_length(name) BETWEEN 1 AND 255),
   CONSTRAINT ck_documents_tier CHECK (redaction_tier IS NULL OR redaction_tier IN ('strict', 'standard', 'minimal')),
   CONSTRAINT ck_documents_detail CHECK (stop_detail IS NULL OR char_length(stop_detail) <= 600),
   CONSTRAINT ck_documents_indexed_bound CHECK (status NOT IN ('indexing', 'indexed') OR account_id IS NOT NULL),
   CONSTRAINT ck_documents_promoted CHECK (status NOT IN ('parsing', 'parsed', 'indexing', 'indexed')
      OR (object_key IS NOT NULL AND sha256 IS NOT NULL))
);
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON documents TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE, DELETE ON documents TO app_rw;
CREATE INDEX ix_documents_tenant_id_account_id ON documents (tenant_id, account_id, created_at DESC);

-- One redacted passage (chunk) of a document with where it came from. The embedding lives in
-- the private ``chunks`` index once the document is bound to an account.
CREATE TABLE document_passages (
   id            bigserial NOT NULL,
   tenant_id     uuid NOT NULL DEFAULT app_tenant(),
   account_id    uuid,
   document_id   uuid NOT NULL,
   seq           integer NOT NULL,
   kind          text NOT NULL,
   location      jsonb NOT NULL,
   heading_path  text,
   content       text NOT NULL,
   content_sha256 text NOT NULL,
   token_count   integer NOT NULL,
   tsv           tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
   created_at    timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_document_passages PRIMARY KEY (id),
   CONSTRAINT uq_document_passages_doc_seq UNIQUE (document_id, seq),
   CONSTRAINT fk_document_passages_document FOREIGN KEY (tenant_id, document_id)
      REFERENCES documents (tenant_id, id) ON DELETE CASCADE,
   CONSTRAINT ck_document_passages_kind CHECK (kind IN ('section', 'transcript_window', 'crm_row', 'text')),
   CONSTRAINT ck_document_passages_sha CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_document_passages_tokens CHECK (token_count >= 0)
);
ALTER TABLE document_passages ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_passages FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON document_passages TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE, DELETE ON document_passages TO app_rw;
GRANT USAGE, SELECT ON SEQUENCE document_passages_id_seq TO app_rw;
CREATE INDEX ix_document_passages_tsv ON document_passages USING gin (tsv);
CREATE INDEX ix_document_passages_account ON document_passages (tenant_id, account_id, document_id, seq);

-- The redaction map: which placeholder replaced which original value in which passage. The
-- original document is kept in object storage; this map lets the Internal Only view reverse it.
CREATE TABLE document_redactions (
   id            bigserial NOT NULL,
   tenant_id     uuid NOT NULL DEFAULT app_tenant(),
   account_id    uuid,
   document_id   uuid NOT NULL,
   passage_seq   integer NOT NULL,
   placeholder   text NOT NULL,
   kind          text NOT NULL,
   original      text NOT NULL,
   created_at    timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_document_redactions PRIMARY KEY (id),
   CONSTRAINT fk_document_redactions_document FOREIGN KEY (tenant_id, document_id)
      REFERENCES documents (tenant_id, id) ON DELETE CASCADE,
   CONSTRAINT ck_document_redactions_original CHECK (char_length(original) <= 500)
);
ALTER TABLE document_redactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_redactions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON document_redactions TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE, DELETE ON document_redactions TO app_rw;
GRANT USAGE, SELECT ON SEQUENCE document_redactions_id_seq TO app_rw;
CREATE INDEX ix_document_redactions_document ON document_redactions (document_id, passage_seq);

-- The documents a file-mode run researches.
CREATE TABLE run_documents (
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   account_id   uuid NOT NULL,
   run_id       uuid NOT NULL,
   document_id  uuid NOT NULL,
   created_at   timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_run_documents PRIMARY KEY (run_id, document_id),
   CONSTRAINT fk_run_documents_run FOREIGN KEY (tenant_id, run_id)
      REFERENCES account_runs (tenant_id, id),
   CONSTRAINT fk_run_documents_document FOREIGN KEY (tenant_id, document_id)
      REFERENCES documents (tenant_id, id) ON DELETE CASCADE
);
ALTER TABLE run_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_documents FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON run_documents TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, DELETE ON run_documents TO app_rw;

-- The private index gains where each chunk came from and which embedding model made its vector.
ALTER TABLE chunks ADD COLUMN passage_seq integer;
ALTER TABLE chunks ADD COLUMN location jsonb;
ALTER TABLE chunks ADD COLUMN heading_path text;
ALTER TABLE chunks ADD COLUMN embedding_model text;

-- A fact read in an uploaded document cites the document and the passage location.
ALTER TABLE fact_evidence ADD COLUMN document_id uuid;
ALTER TABLE fact_evidence ADD COLUMN document_location jsonb;
ALTER TABLE fact_evidence DROP CONSTRAINT ck_fact_evidence_kind;
ALTER TABLE fact_evidence ADD CONSTRAINT ck_fact_evidence_kind
   CHECK (evidence_kind IN ('source', 'inherited', 'search_proof', 'document'));
ALTER TABLE fact_evidence DROP CONSTRAINT ck_fact_evidence_traceable;
ALTER TABLE fact_evidence ADD CONSTRAINT ck_fact_evidence_traceable
   CHECK (evidence_kind IN ('search_proof', 'document')
      OR (url IS NOT NULL AND span_sha256 IS NOT NULL AND source_tier IS NOT NULL
          AND evidence_date IS NOT NULL));
ALTER TABLE fact_evidence ADD CONSTRAINT ck_fact_evidence_document
   CHECK (evidence_kind <> 'document'
      OR (document_id IS NOT NULL AND document_location IS NOT NULL AND span_sha256 IS NOT NULL
          AND url IS NULL));

-- Facts whose document was deleted, waiting for re-evaluation (insert-only).
CREATE TABLE fact_reevaluations (
   id           bigserial NOT NULL,
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   account_id   uuid NOT NULL,
   fact_id      uuid NOT NULL,
   document_id  uuid NOT NULL,
   reason       text NOT NULL,
   created_at   timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_fact_reevaluations PRIMARY KEY (id),
   CONSTRAINT fk_fact_reevaluations_fact FOREIGN KEY (tenant_id, fact_id)
      REFERENCES facts (tenant_id, id),
   CONSTRAINT ck_fact_reevaluations_reason CHECK (reason IN ('source_document_deleted'))
);
ALTER TABLE fact_reevaluations ENABLE ROW LEVEL SECURITY;
ALTER TABLE fact_reevaluations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON fact_reevaluations TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON fact_reevaluations TO app_rw;
GRANT USAGE, SELECT ON SEQUENCE fact_reevaluations_id_seq TO app_rw;

-- Insert-only record that a user deleted a document (hash and size only, never content).
CREATE TABLE document_deletions (
   id           bigserial NOT NULL,
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   account_id   uuid,
   document_id  uuid NOT NULL,
   sha256       text,
   size_bytes   bigint,
   chunks       integer NOT NULL,
   facts_marked integer NOT NULL,
   deleted_by   text NOT NULL,
   created_at   timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_document_deletions PRIMARY KEY (id)
);
ALTER TABLE document_deletions ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_deletions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON document_deletions TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON document_deletions TO app_rw;
GRANT USAGE, SELECT ON SEQUENCE document_deletions_id_seq TO app_rw;

-- The pre-delivery gate's verdict for a run, with its defects (insert-only).
CREATE TABLE run_gate_results (
   id           bigserial NOT NULL,
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   account_id   uuid NOT NULL,
   run_id       uuid NOT NULL,
   verdict      text NOT NULL,
   defects      jsonb NOT NULL,
   created_at   timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_run_gate_results PRIMARY KEY (id),
   CONSTRAINT fk_run_gate_results_run FOREIGN KEY (tenant_id, run_id)
      REFERENCES account_runs (tenant_id, id),
   CONSTRAINT ck_run_gate_results_verdict CHECK (verdict IN ('pass', 'fail')),
   CONSTRAINT ck_run_gate_results_defects CHECK (jsonb_typeof(defects) = 'array')
);
ALTER TABLE run_gate_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_gate_results FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON run_gate_results TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON run_gate_results TO app_rw;
GRANT USAGE, SELECT ON SEQUENCE run_gate_results_id_seq TO app_rw;
CREATE INDEX ix_run_gate_results_run ON run_gate_results (tenant_id, run_id, id DESC);

-- The leak check of one client-facing file against the tenant's private passages (insert-only;
-- hit fingerprints and scores only, never text).
CREATE TABLE leak_checks (
   id           bigserial NOT NULL,
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   account_id   uuid NOT NULL,
   run_id       uuid NOT NULL,
   target       text NOT NULL,
   verdict      text NOT NULL,
   ngram_hits   integer NOT NULL,
   embedding_hits integer NOT NULL,
   max_similarity numeric(6,5) NOT NULL,
   passages_checked integer NOT NULL,
   hits         jsonb NOT NULL,
   created_at   timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_leak_checks PRIMARY KEY (id),
   CONSTRAINT fk_leak_checks_run FOREIGN KEY (tenant_id, run_id)
      REFERENCES account_runs (tenant_id, id),
   CONSTRAINT ck_leak_checks_verdict CHECK (verdict IN ('pass', 'fail')),
   CONSTRAINT ck_leak_checks_counts CHECK (ngram_hits >= 0 AND embedding_hits >= 0 AND passages_checked >= 0)
);
ALTER TABLE leak_checks ENABLE ROW LEVEL SECURITY;
ALTER TABLE leak_checks FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON leak_checks TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON leak_checks TO app_rw;
GRANT USAGE, SELECT ON SEQUENCE leak_checks_id_seq TO app_rw;

-- The Internal Only file of a run: stored apart from the client file set, never in the shared
-- folder or the workspace package.
CREATE TABLE internal_deliverables (
   id           uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   account_id   uuid NOT NULL,
   run_id       uuid NOT NULL,
   label        text NOT NULL,
   object_key   text NOT NULL,
   sha256       text NOT NULL,
   size_bytes   bigint NOT NULL,
   created_at   timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_internal_deliverables PRIMARY KEY (id),
   CONSTRAINT uq_internal_deliverables_run UNIQUE (tenant_id, run_id),
   CONSTRAINT fk_internal_deliverables_run FOREIGN KEY (tenant_id, run_id)
      REFERENCES account_runs (tenant_id, id),
   CONSTRAINT ck_internal_deliverables_key CHECK (object_key ~ '^tenants/[0-9a-f-]{36}/internal/[0-9a-f-]{36}/[0-9a-f]{64}\.docx$'),
   CONSTRAINT ck_internal_deliverables_sha CHECK (sha256 ~ '^[0-9a-f]{64}$')
);
ALTER TABLE internal_deliverables ENABLE ROW LEVEL SECURITY;
ALTER TABLE internal_deliverables FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON internal_deliverables TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON internal_deliverables TO app_rw;

-- Documents whose worker died mid-scan or mid-parse, across tenants, identifiers only (the
-- sweeper then retries each under its own tenant context).
-- reviewed: SECURITY DEFINER returns (tenant_id, document_id) identifiers only, of documents in
-- scanning, parsing or indexing whose lease expired before the given time; search_path pinned;
-- EXECUTE granted to app_rw only.
CREATE FUNCTION documents_orphaned(stale_before timestamptz)
   RETURNS TABLE (tenant_id uuid, document_id uuid)
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT d.tenant_id, d.id FROM documents d
   WHERE d.status IN ('quarantined', 'scanning', 'parsing', 'indexing')
     AND COALESCE(d.lease_expires_at, d.updated_at) < stale_before
   ORDER BY d.updated_at, d.id
$$;
REVOKE ALL ON FUNCTION documents_orphaned(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION documents_orphaned(timestamptz) TO app_rw;
