-- Files of any size (owner decision 2026-10-14, Phase 4B section 3). A file goes from the browser
-- straight to object storage in resumable chunks, then is read in parts: each part is a range of
-- the file (a byte range of a CSV or text file at a record boundary, a range of PDF pages, or the
-- whole file for other types) with its own status, run by a worker in its own sandbox. A part
-- writes its passages with fixed sequence numbers (part_no * 10^7 + n), so a part run again
-- after a crash writes nothing twice. Indexing pages through the passages and keeps its place.
--
-- platform_defaults.max_file_bytes: the per-file limit the admin sets (default 20 GiB; no storage
--   quota by default).
-- document_parts: the parts of one document; the document's owner owns them (private
--   workspaces), written by workers as a system job.

ALTER TABLE platform_defaults
   ADD COLUMN max_file_bytes bigint NOT NULL DEFAULT 21474836480,
   ADD CONSTRAINT ck_platform_defaults_max_file_bytes
      CHECK (max_file_bytes BETWEEN 1048576 AND 5497558138880);

-- passages and their map rows are numbered per part: 64-bit
ALTER TABLE document_passages ALTER COLUMN seq TYPE bigint;
ALTER TABLE document_redactions ALTER COLUMN passage_seq TYPE bigint;
ALTER TABLE chunks ALTER COLUMN passage_seq TYPE bigint;
CREATE INDEX ix_chunks_doc_seq ON chunks (tenant_id, doc_id, passage_seq);

ALTER TABLE documents
   ADD COLUMN parts_total integer NOT NULL DEFAULT 0,
   ADD COLUMN indexed_through bigint,
   ADD CONSTRAINT ck_documents_parts_total CHECK (parts_total >= 0);

CREATE TABLE document_parts (
   tenant_id         uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   owner_user_id     text NOT NULL,
   document_id       uuid NOT NULL,
   part_no           integer NOT NULL,
   start_cursor      jsonb NOT NULL,
   end_offset        bigint,
   end_page          integer,
   status            text NOT NULL DEFAULT 'pending',
   attempts          integer NOT NULL DEFAULT 0,
   passages          integer NOT NULL DEFAULT 0,
   evidence          jsonb,
   failure           text,
   lease_owner       text,
   lease_expires_at  timestamptz,
   started_at        timestamptz,
   finished_at       timestamptz,
   updated_at        timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_document_parts PRIMARY KEY (tenant_id, document_id, part_no),
   CONSTRAINT fk_document_parts_document FOREIGN KEY (tenant_id, document_id)
      REFERENCES documents (tenant_id, id) ON DELETE CASCADE,
   CONSTRAINT ck_document_parts_no CHECK (part_no BETWEEN 0 AND 99999),
   CONSTRAINT ck_document_parts_status CHECK (status IN ('pending', 'running', 'done', 'failed')),
   CONSTRAINT ck_document_parts_failure CHECK ((status = 'failed') = (failure IS NOT NULL)),
   CONSTRAINT ck_document_parts_failure_len CHECK (failure IS NULL OR char_length(failure) <= 200),
   CONSTRAINT ck_document_parts_counts CHECK (attempts >= 0 AND passages >= 0)
);
CREATE INDEX ix_document_parts_tenant_owner ON document_parts (tenant_id, owner_user_id);
CREATE INDEX ix_document_parts_running ON document_parts (tenant_id, status, lease_expires_at);
ALTER TABLE document_parts ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_parts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_owner_isolation ON document_parts TO app_rw
   USING (tenant_id = (SELECT app_tenant())
      AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'))
   WITH CHECK (tenant_id = (SELECT app_tenant())
      AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'));
GRANT SELECT, INSERT, UPDATE, DELETE ON document_parts TO app_rw;

-- Parts whose worker died (lease expired) or that waited for a free worker too long: the sweeper
-- enqueues them again (each part is idempotent).
-- reviewed: SECURITY DEFINER returns (tenant_id, document_id, part_no) identifiers only, of parts of documents still parsing whose lease expired, or that waited, before the given time;
-- search_path pinned; EXECUTE granted to app_rw only (catalogued in tenancy.catalog.JOBS).
CREATE FUNCTION document_parts_orphaned(stale_before timestamptz)
   RETURNS TABLE (tenant_id uuid, document_id uuid, part_no integer)
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT p.tenant_id, p.document_id, p.part_no FROM document_parts p
   JOIN documents d ON d.tenant_id = p.tenant_id AND d.id = p.document_id
   WHERE d.status = 'parsing'
     AND ((p.status = 'running' AND p.lease_expires_at < stale_before)
       OR (p.status = 'pending' AND p.updated_at < stale_before))
   ORDER BY p.updated_at, p.document_id, p.part_no
$$;
REVOKE ALL ON FUNCTION document_parts_orphaned(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION document_parts_orphaned(timestamptz) TO app_rw;
