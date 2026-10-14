-- Everything collected is downloadable (owner decision 2026-10-14, Phase 4B section 6). "Download
-- everything" builds one archive of an account in the background: every deliverable, every
-- table whole (CSV and Excel), and the user's own files and file findings (marked Internal).
-- Raw provider responses are left out (owner decision default). A large archive is split into
-- zip parts; each part is fetched through a short-lived link (a token stored only as its hash,
-- renewed on each status read) and served with range requests.
--
-- download_jobs: one archive build, owned by the user who asked (private workspaces).

CREATE TABLE download_jobs (
   id               uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id        uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   owner_user_id    text NOT NULL DEFAULT app_user(),
   account_id       uuid NOT NULL,
   request_id       uuid NOT NULL,
   status           text NOT NULL DEFAULT 'queued',
   parts            jsonb NOT NULL DEFAULT '[]',
   files            integer NOT NULL DEFAULT 0,
   total_bytes      bigint NOT NULL DEFAULT 0,
   failure          text,
   token_sha256     text,
   token_expires_at timestamptz,
   created_at       timestamptz NOT NULL DEFAULT now(),
   started_at       timestamptz,
   finished_at      timestamptz,
   expires_at       timestamptz NOT NULL DEFAULT now() + interval '7 days',
   CONSTRAINT pk_download_jobs PRIMARY KEY (id),
   CONSTRAINT uq_download_jobs_tenant_id UNIQUE (tenant_id, id),
   CONSTRAINT uq_download_jobs_request UNIQUE (tenant_id, owner_user_id, request_id),
   CONSTRAINT fk_download_jobs_account FOREIGN KEY (tenant_id, account_id)
      REFERENCES accounts (tenant_id, id) ON DELETE CASCADE,
   CONSTRAINT ck_download_jobs_status CHECK (status IN ('queued', 'running', 'ready', 'failed')),
   CONSTRAINT ck_download_jobs_failure CHECK ((status = 'failed') = (failure IS NOT NULL)),
   CONSTRAINT ck_download_jobs_failure_len CHECK (failure IS NULL OR char_length(failure) <= 300),
   CONSTRAINT ck_download_jobs_token CHECK (token_sha256 IS NULL OR token_sha256 ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_download_jobs_counts CHECK (files >= 0 AND total_bytes >= 0)
);
CREATE INDEX ix_download_jobs_tenant_owner ON download_jobs (tenant_id, owner_user_id);
CREATE INDEX ix_download_jobs_account ON download_jobs (tenant_id, account_id, created_at);
ALTER TABLE download_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE download_jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_owner_isolation ON download_jobs TO app_rw
   USING (tenant_id = (SELECT app_tenant())
      AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'))
   WITH CHECK (tenant_id = (SELECT app_tenant())
      AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'));
GRANT SELECT, INSERT, UPDATE, DELETE ON download_jobs TO app_rw;
