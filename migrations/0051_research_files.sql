-- Research files (2026-10-13): a user's own file of any type and size, stored raw in object
-- storage, parsed in resumable units, audited and analysed. Postgres holds metadata, the parsed
-- units, the audit findings, the accumulator checkpoint and the model call log. Every table is
-- tenant scoped with forced row level security.

CREATE TABLE research_files (
   id                uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id         uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   name              text NOT NULL,
   size_bytes        bigint NOT NULL,
   mime              text,
   detected_type     text,
   sha256            text NOT NULL,
   object_key        text NOT NULL,
   status            text NOT NULL DEFAULT 'UPLOADED',
   stop_reason       text,
   stop_detail       text,
   uploaded_by       text NOT NULL,
   units_done        integer NOT NULL DEFAULT 0,
   cursor            jsonb NOT NULL DEFAULT '{}'::jsonb,
   analytics         jsonb,
   estimate          jsonb,
   analysis_approved boolean NOT NULL DEFAULT false,
   lease_owner       text,
   lease_expires_at  timestamptz,
   heartbeat_at      timestamptz,
   created_at        timestamptz NOT NULL DEFAULT now(),
   updated_at        timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_research_files PRIMARY KEY (id),
   CONSTRAINT uq_research_files_tenant_id UNIQUE (tenant_id, id),
   CONSTRAINT uq_research_files_tenant_sha256 UNIQUE (tenant_id, sha256),
   CONSTRAINT ck_research_files_status CHECK (status IN
      ('UPLOADED', 'PARSING', 'AUDITED', 'ANALYZED', 'PARTIAL', 'FAILED')),
   CONSTRAINT ck_research_files_size CHECK (size_bytes >= 0),
   CONSTRAINT ck_research_files_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_research_files_name CHECK (char_length(name) BETWEEN 1 AND 255),
   CONSTRAINT ck_research_files_detail CHECK (stop_detail IS NULL OR char_length(stop_detail) <= 600)
);
ALTER TABLE research_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_files FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON research_files TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE, DELETE ON research_files TO app_rw;
CREATE INDEX ix_research_files_tenant_id_created_at ON research_files (tenant_id, created_at DESC);

CREATE TABLE research_file_uploads (
   id             uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id      uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   name           text NOT NULL,
   size_bytes     bigint NOT NULL,
   mime           text,
   part_size      integer NOT NULL,
   parts_total    integer NOT NULL,
   status         text NOT NULL DEFAULT 'open',
   file_id        uuid,
   uploaded_by    text NOT NULL,
   created_at     timestamptz NOT NULL DEFAULT now(),
   expires_at     timestamptz NOT NULL,
   CONSTRAINT pk_research_file_uploads PRIMARY KEY (id),
   CONSTRAINT uq_research_file_uploads_tenant_id UNIQUE (tenant_id, id),
   CONSTRAINT ck_research_file_uploads_status CHECK (status IN ('open', 'completed', 'aborted')),
   CONSTRAINT ck_research_file_uploads_parts CHECK (parts_total >= 1 AND part_size >= 1),
   CONSTRAINT ck_research_file_uploads_size CHECK (size_bytes >= 0)
);
ALTER TABLE research_file_uploads ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_file_uploads FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON research_file_uploads TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE, DELETE ON research_file_uploads TO app_rw;

CREATE TABLE research_file_upload_parts (
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   upload_id    uuid NOT NULL,
   part_no      integer NOT NULL,
   size_bytes   integer NOT NULL,
   sha256       text NOT NULL,
   created_at   timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_research_file_upload_parts PRIMARY KEY (upload_id, part_no),
   CONSTRAINT fk_research_file_upload_parts_upload FOREIGN KEY (tenant_id, upload_id)
      REFERENCES research_file_uploads (tenant_id, id) ON DELETE CASCADE,
   CONSTRAINT ck_research_file_upload_parts_no CHECK (part_no >= 0)
);
ALTER TABLE research_file_upload_parts ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_file_upload_parts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON research_file_upload_parts TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE, DELETE ON research_file_upload_parts TO app_rw;

-- One parsed unit (a batch of rows, a page, a slide, a section, an archive member note). Written
-- in the same transaction as its findings and the accumulator checkpoint (write-ahead).
CREATE TABLE research_file_chunks (
   id            bigserial NOT NULL,
   tenant_id     uuid NOT NULL DEFAULT app_tenant(),
   file_id       uuid NOT NULL,
   seq           integer NOT NULL,
   kind          text NOT NULL,
   location      jsonb NOT NULL,
   content       text NOT NULL,
   content_hash  text NOT NULL,
   stats         jsonb NOT NULL DEFAULT '{}'::jsonb,
   tsv           tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
   created_at    timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_research_file_chunks PRIMARY KEY (id),
   CONSTRAINT uq_research_file_chunks_file_seq UNIQUE (file_id, seq),
   CONSTRAINT fk_research_file_chunks_file FOREIGN KEY (tenant_id, file_id)
      REFERENCES research_files (tenant_id, id) ON DELETE CASCADE
);
ALTER TABLE research_file_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_file_chunks FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON research_file_chunks TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, DELETE ON research_file_chunks TO app_rw;
GRANT USAGE, SELECT ON SEQUENCE research_file_chunks_id_seq TO app_rw;
CREATE INDEX ix_research_file_chunks_tsv ON research_file_chunks USING gin (tsv);

-- The accumulator checkpoint (column profiles, duplicate hashes, document analytics), zstd
-- compressed, rewritten in the same transaction as each unit: a resumed parse starts from exactly
-- the state an uninterrupted parse would have.
CREATE TABLE research_file_state (
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   file_id      uuid NOT NULL,
   state        bytea NOT NULL,
   updated_at   timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_research_file_state PRIMARY KEY (file_id),
   CONSTRAINT fk_research_file_state_file FOREIGN KEY (tenant_id, file_id)
      REFERENCES research_files (tenant_id, id) ON DELETE CASCADE
);
ALTER TABLE research_file_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_file_state FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON research_file_state TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE, DELETE ON research_file_state TO app_rw;

CREATE TABLE research_file_findings (
   id          bigserial NOT NULL,
   tenant_id   uuid NOT NULL DEFAULT app_tenant(),
   file_id     uuid NOT NULL,
   seq         integer NOT NULL,
   severity    text NOT NULL,
   check_id    text NOT NULL,
   location    jsonb NOT NULL,
   message     text NOT NULL,
   fix         text,
   created_at  timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_research_file_findings PRIMARY KEY (id),
   CONSTRAINT fk_research_file_findings_file FOREIGN KEY (tenant_id, file_id)
      REFERENCES research_files (tenant_id, id) ON DELETE CASCADE,
   CONSTRAINT ck_research_file_findings_severity CHECK (severity IN ('info', 'warn', 'error')),
   CONSTRAINT ck_research_file_findings_message CHECK (char_length(message) <= 1000)
);
ALTER TABLE research_file_findings ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_file_findings FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON research_file_findings TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, DELETE ON research_file_findings TO app_rw;
GRANT USAGE, SELECT ON SEQUENCE research_file_findings_id_seq TO app_rw;
CREATE INDEX ix_research_file_findings_file ON research_file_findings (file_id, severity, id);

-- Model calls: a 'started' row before the request (write-ahead), then the reply with tokens and
-- cost. The cache key is the task, the model and the content hash: a chunk seen before (in this
-- or any other file of the tenant) costs nothing.
CREATE TABLE research_file_model_calls (
   id             bigserial NOT NULL,
   tenant_id      uuid NOT NULL DEFAULT app_tenant(),
   file_id        uuid,
   task           text NOT NULL,
   model          text NOT NULL,
   cache_key      text NOT NULL,
   served_from    text NOT NULL,
   input_tokens   integer NOT NULL DEFAULT 0,
   output_tokens  integer NOT NULL DEFAULT 0,
   cost_usd       numeric(14,6),
   reply_text     text,
   created_at     timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_research_file_model_calls PRIMARY KEY (id),
   CONSTRAINT ck_research_file_model_calls_served CHECK (served_from IN ('started', 'model', 'cache')),
   CONSTRAINT ck_research_file_model_calls_tokens CHECK (input_tokens >= 0 AND output_tokens >= 0)
);
ALTER TABLE research_file_model_calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_file_model_calls FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON research_file_model_calls TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, DELETE ON research_file_model_calls TO app_rw;
GRANT USAGE, SELECT ON SEQUENCE research_file_model_calls_id_seq TO app_rw;
CREATE INDEX ix_research_file_model_calls_key ON research_file_model_calls
   (tenant_id, cache_key, created_at DESC) WHERE served_from = 'model';

-- Insert-only record that a user deleted a file (hash and size only, never content).
CREATE TABLE research_file_deletions (
   id           bigserial NOT NULL,
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   file_id      uuid NOT NULL,
   sha256       text NOT NULL,
   size_bytes   bigint NOT NULL,
   deleted_by   text NOT NULL,
   created_at   timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_research_file_deletions PRIMARY KEY (id)
);
ALTER TABLE research_file_deletions ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_file_deletions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON research_file_deletions TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON research_file_deletions TO app_rw;
GRANT USAGE, SELECT ON SEQUENCE research_file_deletions_id_seq TO app_rw;

-- Files whose worker died mid-parse, across tenants, identifiers only (the sweeper then ends each
-- under its own tenant context).
-- reviewed: SECURITY DEFINER returns (tenant_id, file_id) identifiers only, of files in PARSING
-- whose lease expired before the given time; search_path pinned; EXECUTE granted to app_rw only.
CREATE FUNCTION research_files_orphaned(stale_before timestamptz)
   RETURNS TABLE (tenant_id uuid, file_id uuid)
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT f.tenant_id, f.id FROM research_files f
   WHERE f.status = 'PARSING'
     AND COALESCE(f.lease_expires_at, f.updated_at) < stale_before
   ORDER BY f.updated_at, f.id
$$;
REVOKE ALL ON FUNCTION research_files_orphaned(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION research_files_orphaned(timestamptz) TO app_rw;
