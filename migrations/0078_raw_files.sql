-- Migration 0078: the raw file store (brief B1, 2026-10-17).
-- The uploaded bytes are the golden record, kept content-addressed: one object per (tenant,
-- sha256). Re-uploading the same bytes registers no second copy (idempotent); a changed file is
-- a new sha256, so a superseded version is kept until its own document is deleted. A document
-- row points at it by sha256; deleting a document removes the raw object and its registry row
-- only when no other document of the tenant shares the hash.
CREATE TABLE raw_files (
   tenant_id         uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   owner_user_id     text NOT NULL DEFAULT app_user(),
   sha256            text NOT NULL,
   size_bytes        bigint NOT NULL,
   object_key        text NOT NULL,
   first_document_id uuid,
   uploaded_at       timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_raw_files PRIMARY KEY (tenant_id, sha256),
   CONSTRAINT fk_raw_files_first_document FOREIGN KEY (tenant_id, first_document_id)
      REFERENCES documents (tenant_id, id) ON DELETE SET NULL (first_document_id),
   CONSTRAINT ck_raw_files_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_raw_files_size CHECK (size_bytes > 0)
);
CREATE INDEX ix_raw_files_tenant_owner ON raw_files (tenant_id, owner_user_id);
ALTER TABLE raw_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw_files FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_owner_isolation ON raw_files TO app_rw
   USING (tenant_id = (SELECT app_tenant())
      AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'))
   WITH CHECK (tenant_id = (SELECT app_tenant())
      AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'));
GRANT SELECT, INSERT, DELETE ON raw_files TO app_rw;
