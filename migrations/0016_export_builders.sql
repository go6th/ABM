-- Migration 0016: export builders storage (expand only; no column dropped or altered).
-- Spec docs/prompts/build_export_builders.md section 6. Numbered 0016 because 0015 (spend) is
-- committed on the unmerged credit-cap branch; down_revision is 0014 here and is re-pointed to
-- 0015 when that branch is merged.
--
-- * facts gains a nullable `lane` so the Summary can group graded rows lane by lane (written by
--   grading's record_graded_fact once it carries the lane: a listed follow-up).
-- * report_sections: the distill step's authored text (narrative, caveats, hypotheses with their
--   confirming questions, first-call questions, what would change a decision, executive voice),
--   versioned per run, append-only. The builders only read it.
-- * deliverables: one row per stored file of a delivered set, append-only (a redelivery adds a
--   version that supersedes the previous one; nothing is updated or deleted). The object key is
--   neutral: tenant/account/run/deliverables/<sha256>.<ext>, never a provider or broker name.
-- Both tables: tenant_id NOT NULL, forced RLS (USING and WITH CHECK, TO app_rw), SELECT and
-- INSERT only. Rollback: 0016_export_builders.down.sql.

ALTER TABLE facts
   ADD COLUMN lane text,
   ADD CONSTRAINT ck_facts_lane_shape CHECK (lane IS NULL OR lane ~ '^[a-z][a-z0-9_]{0,40}$');

CREATE TABLE report_sections (
   id                 uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id          uuid NOT NULL DEFAULT app_tenant(),
   account_id         uuid NOT NULL,
   run_id             uuid NOT NULL,
   version            integer NOT NULL,
   kind               text NOT NULL,
   position           integer NOT NULL,
   text               text NOT NULL,
   cited_source_codes text[] NOT NULL DEFAULT '{}',
   fact_ids           uuid[] NOT NULL DEFAULT '{}',
   confirming_question text,
   speaker            text,
   visibility         text NOT NULL DEFAULT 'public',
   created_at         timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_report_sections PRIMARY KEY (id),
   CONSTRAINT fk_report_sections_tenant_id_account_id_accounts
      FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
   CONSTRAINT uq_report_sections_position UNIQUE (tenant_id, run_id, version, kind, position),
   CONSTRAINT ck_report_sections_version CHECK (version >= 1),
   CONSTRAINT ck_report_sections_position CHECK (position >= 0),
   CONSTRAINT ck_report_sections_kind CHECK (kind IN ('narrative', 'caveat', 'executive_voice',
      'hypothesis', 'first_call_question', 'decision_change')),
   CONSTRAINT ck_report_sections_text CHECK (char_length(text) BETWEEN 1 AND 8000),
   CONSTRAINT ck_report_sections_codes CHECK (
      array_to_string(cited_source_codes, ',') ~ '^(S-[0-9]{4,}(,S-[0-9]{4,})*)?$'),
   CONSTRAINT ck_report_sections_question CHECK (kind <> 'hypothesis'
      OR char_length(confirming_question) BETWEEN 1 AND 2000),
   CONSTRAINT ck_report_sections_speaker CHECK (kind <> 'executive_voice'
      OR char_length(speaker) BETWEEN 1 AND 200),
   CONSTRAINT ck_report_sections_visibility CHECK (visibility IN ('public', 'private'))
);
CREATE INDEX ix_report_sections_run ON report_sections (tenant_id, run_id, version);
ALTER TABLE report_sections ENABLE ROW LEVEL SECURITY;
ALTER TABLE report_sections FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON report_sections TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON report_sections TO app_rw;

CREATE TABLE deliverables (
   id            uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id     uuid NOT NULL DEFAULT app_tenant(),
   account_id    uuid NOT NULL,
   run_id        uuid NOT NULL,
   set_hash      text NOT NULL,
   kind          text NOT NULL,
   version       integer NOT NULL,
   supersedes    uuid,
   filename      text NOT NULL,
   sha256        text NOT NULL,
   size_bytes    bigint NOT NULL,
   object_key    text NOT NULL,
   manifest_json jsonb NOT NULL,
   content_hash  text NOT NULL,
   snapshot_hash text NOT NULL,
   theme_hash    text NOT NULL,
   rules_hash    text NOT NULL,
   built_at      timestamptz NOT NULL,
   created_at    timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_deliverables PRIMARY KEY (id),
   CONSTRAINT uq_deliverables_tenant_id_id UNIQUE (tenant_id, id),
   CONSTRAINT fk_deliverables_tenant_id_account_id_accounts
      FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
   CONSTRAINT fk_deliverables_supersedes
      FOREIGN KEY (tenant_id, supersedes) REFERENCES deliverables (tenant_id, id),
   -- a repeated build of the same content returns the stored set (B14)
   CONSTRAINT uq_deliverables_set_kind UNIQUE (tenant_id, run_id, set_hash, kind),
   -- one version number per account and kind; a redelivery is the next version (B14)
   CONSTRAINT uq_deliverables_version UNIQUE (tenant_id, account_id, kind, version),
   CONSTRAINT ck_deliverables_kind CHECK (kind IN ('summary', 'evidence', 'people', 'jobs',
      'tech', 'workspace')),
   CONSTRAINT ck_deliverables_version CHECK (version >= 1),
   CONSTRAINT ck_deliverables_supersedes CHECK ((supersedes IS NULL) = (version = 1)),
   CONSTRAINT ck_deliverables_not_self CHECK (supersedes IS NULL OR supersedes <> id),
   CONSTRAINT ck_deliverables_hashes CHECK (sha256 ~ '^[0-9a-f]{64}$'
      AND set_hash ~ '^[0-9a-f]{64}$' AND content_hash ~ '^[0-9a-f]{64}$'
      AND snapshot_hash ~ '^[0-9a-f]{64}$' AND theme_hash ~ '^[0-9a-f]{64}$'
      AND rules_hash ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_deliverables_size CHECK (size_bytes >= 0),
   CONSTRAINT ck_deliverables_object_key CHECK (object_key ~
      '^[0-9a-f-]{36}/[0-9a-f-]{36}/[0-9a-f-]{36}/deliverables/[0-9a-f]{64}\.(docx|xlsx|zip)$'),
   CONSTRAINT ck_deliverables_filename CHECK (filename ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,150}$')
);
CREATE INDEX ix_deliverables_account_kind ON deliverables (tenant_id, account_id, kind, version);
ALTER TABLE deliverables ENABLE ROW LEVEL SECURITY;
ALTER TABLE deliverables FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON deliverables TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON deliverables TO app_rw;
