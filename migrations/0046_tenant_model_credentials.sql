-- Optional per-tenant BYO model keys; only KMS ciphertext is stored in this tenant-owned table.

CREATE TABLE tenant_model_credentials (
   tenant_id   uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   provider    text NOT NULL,
   ciphertext  bytea NOT NULL,
   updated_by  text NOT NULL,
   created_at  timestamptz NOT NULL DEFAULT now(),
   updated_at  timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_tenant_model_credentials PRIMARY KEY (tenant_id, provider),
   CONSTRAINT ck_tenant_model_credentials_provider CHECK (provider = 'anthropic'),
   CONSTRAINT ck_tenant_model_credentials_ciphertext CHECK (octet_length(ciphertext) > 0)
);
ALTER TABLE tenant_model_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_model_credentials FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenant_model_credentials TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_model_credentials TO app_rw;
