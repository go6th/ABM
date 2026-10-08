-- Migration 0036: people data and retention (cross-cutting X4; expand only). Spec
-- docs/prompts/build_crosscutting.md s3 PD1 to PD5, D-83 and D-84. down_revision 0035.
--
-- retention_runs: one dry-run or execute pass, resumable by cursor.
-- retention_actions: insert-only; what a run removed (class, table, row id).
-- tombstones: insert-only; proof of deletion with no content (ids, type, time,
--   reason, content hash). Idempotent per subject.
-- dsar_requests / dsar_events: the DSAR workflow and its audited steps. The
--   request's tenant_scope is the recorded grant; without it cross-tenant reads
--   are refused (privacy.dsar.scoped_search).
-- suppression_entries: insert-only salted hashes (kind url|name), never values.
-- Platform tables (no tenant): operator-plane data. people.erasure columns exist.

CREATE TABLE retention_runs (
   id         bigserial NOT NULL,
   mode       text NOT NULL CHECK (mode IN ('dry_run', 'execute')),
   status     text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'failed')),
   cursor     bigint NOT NULL DEFAULT 0,
   counts     jsonb NOT NULL DEFAULT '{}'::jsonb,
   started_at timestamptz NOT NULL DEFAULT now(),
   finished_at timestamptz,
   CONSTRAINT pk_retention_runs PRIMARY KEY (id)
);

CREATE TABLE retention_actions (
   id      bigserial NOT NULL,
   run_id  bigint NOT NULL,
   cls     text NOT NULL,
   table_name text NOT NULL,
   row_id  text NOT NULL,
   at      timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_retention_actions PRIMARY KEY (id),
   CONSTRAINT fk_retention_actions_run FOREIGN KEY (run_id) REFERENCES retention_runs (id),
   CONSTRAINT uq_retention_actions UNIQUE (run_id, table_name, row_id)
);

CREATE TABLE tombstones (
   id             bigserial NOT NULL,
   subject_type   text NOT NULL,
   subject_id     text NOT NULL,
   tenant_id      uuid,
   reason         text NOT NULL,
   content_sha256 text,
   at             timestamptz NOT NULL,
   CONSTRAINT pk_tombstones PRIMARY KEY (id),
   CONSTRAINT uq_tombstones_subject UNIQUE (subject_type, subject_id)
);

CREATE TABLE dsar_requests (
   id           bigserial NOT NULL,
   kind         text NOT NULL CHECK (kind IN ('access', 'erasure')),
   subject_ref  text NOT NULL,
   tenant_scope uuid,
   status       text NOT NULL DEFAULT 'received'
                CHECK (status IN ('received', 'verified', 'done', 'refused')),
   opened_at    timestamptz NOT NULL DEFAULT now(),
   deadline_at  timestamptz NOT NULL,
   closed_at    timestamptz,
   CONSTRAINT pk_dsar_requests PRIMARY KEY (id)
);

CREATE TABLE dsar_events (
   id         bigserial NOT NULL,
   request_id bigint NOT NULL,
   step       text NOT NULL,
   detail     jsonb NOT NULL DEFAULT '{}'::jsonb,
   at         timestamptz NOT NULL,
   CONSTRAINT pk_dsar_events PRIMARY KEY (id),
   CONSTRAINT fk_dsar_events_request FOREIGN KEY (request_id) REFERENCES dsar_requests (id)
);

CREATE TABLE suppression_entries (
   id         bigserial NOT NULL,
   kind       text NOT NULL CHECK (kind IN ('url', 'name')),
   hash       text NOT NULL,
   reason     text NOT NULL,
   created_at timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_suppression_entries PRIMARY KEY (id),
   CONSTRAINT uq_suppression_entries UNIQUE (kind, hash)
);

GRANT SELECT, INSERT ON retention_runs, retention_actions, tombstones, dsar_requests,
   dsar_events, suppression_entries TO app_rw;
GRANT USAGE ON SEQUENCE retention_runs_id_seq, retention_actions_id_seq, tombstones_id_seq,
   dsar_requests_id_seq, dsar_events_id_seq, suppression_entries_id_seq TO app_rw;
GRANT UPDATE ON dsar_requests TO app_rw;  -- status transitions only (conditional UPDATE WHERE)
REVOKE DELETE ON retention_runs, retention_actions, tombstones, dsar_requests, dsar_events,
   suppression_entries FROM app_rw;
REVOKE UPDATE ON retention_runs, retention_actions, tombstones, dsar_events,
   suppression_entries FROM app_rw;
