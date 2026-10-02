-- Migration 0012: job posting pipeline storage (expand only; no column dropped or altered).
-- Spec build_job_posting_pipeline.md section 6. job_postings (created in 0010) gains the posting
-- columns; four append-only tables hold platform sentences, classifications, observations and the
-- run-level pipeline record. All tenant tables: tenant_id NOT NULL, forced RLS (USING and WITH
-- CHECK, TO app_rw), INSERT and SELECT only (no UPDATE, no DELETE). Posting text itself lives in
-- the payload store (text_ref, text_sha256), never in the database.
-- Rollback: 0012_job_pipeline.down.sql.

ALTER TABLE job_postings
   ADD COLUMN url            text,
   ADD COLUMN other_urls     text[] NOT NULL DEFAULT '{}',
   ADD COLUMN title          text,
   ADD COLUMN location       text,
   ADD COLUMN posted_at      date,
   ADD COLUMN first_seen     date,
   ADD COLUMN status_source  text,
   ADD COLUMN text_ref       text,
   ADD COLUMN text_sha256    text,
   ADD COLUMN text_complete  boolean,
   ADD COLUMN full_text_read boolean NOT NULL DEFAULT false,
   ADD COLUMN posting_class  text,
   ADD COLUMN lanes          text[] NOT NULL DEFAULT '{}',
   ADD COLUMN platforms      text[] NOT NULL DEFAULT '{}',
   ADD COLUMN title_only     boolean NOT NULL DEFAULT false,
   ADD COLUMN weak_key       boolean NOT NULL DEFAULT false,
   ADD COLUMN undated        boolean NOT NULL DEFAULT false,
   ADD COLUMN hostile        boolean NOT NULL DEFAULT false,
   ADD CONSTRAINT ck_job_postings_url CHECK (url IS NOT NULL AND url ~ '^https?://') NOT VALID,
   ADD CONSTRAINT ck_job_postings_class CHECK (posting_class IS NULL OR posting_class IN
      ('build', 'admin_owner', 'working_tool', 'user', 'unclassified')),
   ADD CONSTRAINT ck_job_postings_status_source CHECK (status_source IS NULL OR status_source IN
      ('live_board', 'feed', 'unknown')),
   ADD CONSTRAINT ck_job_postings_text_sha256 CHECK (text_sha256 IS NULL OR text_sha256 ~ '^[0-9a-f]{64}$');
ALTER TABLE job_postings VALIDATE CONSTRAINT ck_job_postings_url;
CREATE INDEX ix_job_postings_account_id_posting_key ON job_postings (account_id, posting_key);

CREATE TABLE job_posting_platform_sentences (
   id          bigserial NOT NULL,
   tenant_id   uuid NOT NULL DEFAULT app_tenant(),
   run_id      uuid NOT NULL,
   posting_key text NOT NULL,
   platform    text NOT NULL,
   sentence    text NOT NULL,
   start_offset integer NOT NULL,
   end_offset  integer NOT NULL,
   span_sha256 text NOT NULL,
   created_at  timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_job_posting_platform_sentences PRIMARY KEY (id),
   CONSTRAINT fk_job_posting_platform_sentences_posting
      FOREIGN KEY (tenant_id, run_id, posting_key) REFERENCES job_postings (tenant_id, run_id, posting_key),
   CONSTRAINT ck_job_posting_platform_sentences_offsets CHECK (start_offset >= 0 AND end_offset > start_offset),
   CONSTRAINT ck_job_posting_platform_sentences_len CHECK (char_length(sentence) BETWEEN 1 AND 2000)
);
ALTER TABLE job_posting_platform_sentences ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_posting_platform_sentences FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON job_posting_platform_sentences TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON job_posting_platform_sentences TO app_rw;
GRANT USAGE ON SEQUENCE job_posting_platform_sentences_id_seq TO app_rw;

CREATE TABLE job_posting_classifications (
   id                bigserial NOT NULL,
   tenant_id         uuid NOT NULL DEFAULT app_tenant(),
   run_id            uuid NOT NULL,
   posting_key       text NOT NULL,
   posting_class     text NOT NULL,
   classified_by     text NOT NULL,
   reason            text NOT NULL,
   rule_id           text,
   evidence_sentence text,
   second_pass_label text,
   created_at        timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_job_posting_classifications PRIMARY KEY (id),
   CONSTRAINT fk_job_posting_classifications_posting
      FOREIGN KEY (tenant_id, run_id, posting_key) REFERENCES job_postings (tenant_id, run_id, posting_key),
   CONSTRAINT ck_job_posting_classifications_class CHECK (posting_class IN
      ('build', 'admin_owner', 'working_tool', 'user', 'unclassified')),
   CONSTRAINT ck_job_posting_classifications_by CHECK (classified_by IN ('rule', 'extractor', 'none'))
);
ALTER TABLE job_posting_classifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_posting_classifications FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON job_posting_classifications TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON job_posting_classifications TO app_rw;
GRANT USAGE ON SEQUENCE job_posting_classifications_id_seq TO app_rw;

CREATE TABLE job_posting_observations (
   id              bigserial NOT NULL,
   tenant_id       uuid NOT NULL DEFAULT app_tenant(),
   account_id      uuid NOT NULL,
   run_id          uuid NOT NULL,
   posting_key     text NOT NULL,
   seen_at         timestamptz NOT NULL,
   source_category text NOT NULL,
   status_seen     text NOT NULL,
   raw_ref         text NOT NULL,
   created_at      timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_job_posting_observations PRIMARY KEY (id),
   CONSTRAINT fk_job_posting_observations_tenant_id_account_id_accounts
      FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
   CONSTRAINT ck_job_posting_observations_status CHECK (status_seen IN ('open', 'closed', 'unknown')),
   CONSTRAINT ck_job_posting_observations_category CHECK (source_category IN ('live_board', 'feed', 'unknown'))
);
CREATE INDEX ix_job_posting_observations_account_id_posting_key
   ON job_posting_observations (account_id, posting_key);
ALTER TABLE job_posting_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_posting_observations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON job_posting_observations TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON job_posting_observations TO app_rw;
GRANT USAGE ON SEQUENCE job_posting_observations_id_seq TO app_rw;

CREATE TABLE job_pipeline_runs (
   run_id       uuid NOT NULL,
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   account_id   uuid NOT NULL,
   as_of        date NOT NULL,
   window_start date NOT NULL,
   config_hash  text NOT NULL,
   record_json  jsonb NOT NULL,     -- the PipelineResult (no posting text, no provider names)
   created_at   timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_job_pipeline_runs PRIMARY KEY (run_id),
   CONSTRAINT fk_job_pipeline_runs_tenant_id_account_id_accounts
      FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
   CONSTRAINT ck_job_pipeline_runs_window CHECK (window_start <= as_of)
);
ALTER TABLE job_pipeline_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_pipeline_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON job_pipeline_runs TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON job_pipeline_runs TO app_rw;
