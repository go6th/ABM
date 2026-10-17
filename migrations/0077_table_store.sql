-- Migration 0077: the table store (tabular questions, 2026-10-17).
-- A spreadsheet or CRM export is queried, not retrieved: every row of an ingested table is kept
-- whole and typed, so a filter, count or lookup over it returns the complete set of matching
-- rows (query_table) instead of a top-k sample of passages. Keyed by (document, member, table);
-- the crm_row passages stay for citation and keyword search.
--
-- document_passages.metadata: a crm_row's cells, typed, under the normalised column names (what
--   metadata filters read; NULL for the other kinds and for a row's payload parts).
-- document_tables: one row per ingested table: its columns (name as written, normalised name,
--   inferred type date | number | text, whether it is payload), the business key duplicate rows
--   collapse on, and the row count.
-- document_table_rows: the rows, one jsonb object of typed cells each, with the passage (seq)
--   and the part that wrote them (a part read again removes its own rows first).
-- Both are owned by the document's owner (private workspaces, 0063): the writer (documents.repo)
-- copies documents.owner_user_id on insert, as document_parts does (no fill trigger).
ALTER TABLE document_passages ADD COLUMN metadata jsonb;

CREATE TABLE document_tables (
   id              uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id       uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   owner_user_id   text NOT NULL DEFAULT app_user(),
   account_id      uuid,
   document_id     uuid NOT NULL,
   member          text,
   table_name      text NOT NULL,
   columns         jsonb NOT NULL DEFAULT '[]'::jsonb,
   payload_columns jsonb NOT NULL DEFAULT '[]'::jsonb,
   business_key    text,
   row_count       integer NOT NULL DEFAULT 0,
   created_at      timestamptz NOT NULL DEFAULT now(),
   updated_at      timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_document_tables PRIMARY KEY (tenant_id, id),
   CONSTRAINT uq_document_tables_document_table UNIQUE (document_id, table_name),
   CONSTRAINT fk_document_tables_document FOREIGN KEY (tenant_id, document_id)
      REFERENCES documents (tenant_id, id) ON DELETE CASCADE,
   CONSTRAINT ck_document_tables_row_count CHECK (row_count >= 0),
   CONSTRAINT ck_document_tables_name CHECK (char_length(table_name) BETWEEN 1 AND 500)
);
CREATE INDEX ix_document_tables_account ON document_tables (tenant_id, account_id, document_id);
CREATE INDEX ix_document_tables_tenant_owner ON document_tables (tenant_id, owner_user_id);
ALTER TABLE document_tables ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_tables FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_owner_isolation ON document_tables TO app_rw
   USING (tenant_id = (SELECT app_tenant())
      AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'))
   WITH CHECK (tenant_id = (SELECT app_tenant())
      AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'));
GRANT SELECT, INSERT, UPDATE, DELETE ON document_tables TO app_rw;

CREATE TABLE document_table_rows (
   tenant_id       uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   owner_user_id   text NOT NULL DEFAULT app_user(),
   account_id      uuid,
   document_id     uuid NOT NULL,
   table_id        uuid NOT NULL,
   part_no         integer NOT NULL,
   row_no          bigint NOT NULL,
   seq             bigint NOT NULL,
   cells           jsonb NOT NULL,
   CONSTRAINT pk_document_table_rows PRIMARY KEY (tenant_id, table_id, row_no),
   CONSTRAINT fk_document_table_rows_table FOREIGN KEY (tenant_id, table_id)
      REFERENCES document_tables (tenant_id, id) ON DELETE CASCADE,
   CONSTRAINT fk_document_table_rows_document FOREIGN KEY (tenant_id, document_id)
      REFERENCES documents (tenant_id, id) ON DELETE CASCADE,
   CONSTRAINT ck_document_table_rows_cells CHECK (jsonb_typeof(cells) = 'object')
);
CREATE INDEX ix_document_table_rows_part ON document_table_rows (tenant_id, document_id, part_no);
CREATE INDEX ix_document_table_rows_tenant_owner ON document_table_rows (tenant_id, owner_user_id);
ALTER TABLE document_table_rows ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_table_rows FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_owner_isolation ON document_table_rows TO app_rw
   USING (tenant_id = (SELECT app_tenant())
      AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'))
   WITH CHECK (tenant_id = (SELECT app_tenant())
      AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'));
GRANT SELECT, INSERT, UPDATE, DELETE ON document_table_rows TO app_rw;
