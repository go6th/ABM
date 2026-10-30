-- Account bundles (2026-10-27): one row per rendered version of an account's output parity
-- bundle (src/parity). A version is written once a run's build node (or an analyst's edit)
-- has rendered the files, recalculated the workbooks and run the gate; ``status`` says whether
-- the gate passed (built: the files are served) or failed (nothing of it is served; the gate
-- report says why). Insert-only: a correction writes the next version, a version is never
-- rewritten. The bundle document, the rendered files and the manifest live in the payload
-- store under tenants/<tenant>/accounts/<account>/runs/<run>/parity/v<version>/ (``bundle_key``
-- names the bundle document); ``manifest_json`` is the file manifest (name, size, SHA-256,
-- MIME type, bundle version, the recalculated Summary values) and ``gate_json`` the gate
-- report (counts and kinds only, never a name or a passage).
CREATE TABLE account_bundles (
   id             bigserial NOT NULL,
   tenant_id      uuid NOT NULL DEFAULT app_tenant(),
   owner_user_id  text NOT NULL DEFAULT app_user(),
   account_id     uuid NOT NULL,
   run_id         uuid NOT NULL,
   version        integer NOT NULL,
   bundle_key     text NOT NULL,
   manifest_json  jsonb NOT NULL DEFAULT '{}'::jsonb,
   gate_json      jsonb NOT NULL DEFAULT '{}'::jsonb,
   status         text NOT NULL,
   created_at     timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_account_bundles PRIMARY KEY (id),
   CONSTRAINT fk_account_bundles_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT fk_account_bundles_tenant_id_account_id_accounts
      FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
   CONSTRAINT uq_account_bundles_version UNIQUE (tenant_id, account_id, version),
   CONSTRAINT ck_account_bundles_version CHECK (version >= 1),
   CONSTRAINT ck_account_bundles_status CHECK (status IN ('built', 'failed')),
   CONSTRAINT ck_account_bundles_bundle_key CHECK (
      bundle_key ~ '^tenants/[0-9a-f-]{36}/accounts/[0-9a-f-]{36}/runs/[0-9a-f-]{36}/parity/v[0-9]+/bundle\.json$'
   )
);
COMMENT ON TABLE account_bundles IS 'Rendered versions of an account''s output parity bundle: payload key, manifest, gate report, built or failed; insert-only.';
COMMENT ON COLUMN account_bundles.bundle_key IS 'Payload store key of the bundle document; the rendered files and manifest sit beside it.';
CREATE INDEX ix_account_bundles_lookup ON account_bundles (tenant_id, account_id, version DESC);
CREATE INDEX ix_account_bundles_run ON account_bundles (tenant_id, run_id);
CREATE INDEX ix_account_bundles_tenant_owner ON account_bundles (tenant_id, owner_user_id);
ALTER TABLE account_bundles ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_bundles FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_owner_isolation ON account_bundles TO app_rw
   USING (tenant_id = (SELECT app_tenant())
      AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'))
   WITH CHECK (tenant_id = (SELECT app_tenant())
      AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'));
GRANT SELECT, INSERT ON account_bundles TO app_rw;   -- insert-only: a version is never rewritten
GRANT USAGE ON SEQUENCE account_bundles_id_seq TO app_rw;
