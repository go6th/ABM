-- migrations/0003_accounts_extension.sql
-- Data layer v1, step 7: v1 account attributes on the starter accounts table.
-- RLS for accounts was applied in 0002; nothing here relaxes it.
ALTER TABLE accounts RENAME COLUMN careers_verified_at TO careers_url_verified_at;

ALTER TABLE accounts
  ADD COLUMN name            text,
  ADD COLUMN industry        text,
  ADD COLUMN primary_lane    text,
  ADD COLUMN pursuing_client text,
  ADD COLUMN created_at      timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN updated_at      timestamptz NOT NULL DEFAULT now();

-- Application inserts take the tenant from the transaction context; RLS WITH CHECK
-- still rejects any explicit value that does not match it.
ALTER TABLE accounts ALTER COLUMN tenant_id SET DEFAULT app_tenant();

-- A domain is a bare hostname: lowercase, no scheme, port, path or www prefix.
ALTER TABLE accounts
  ADD CONSTRAINT ck_accounts_domain_shape CHECK (
    domain = lower(domain)
    AND domain ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'
    AND domain NOT LIKE 'www.%'
  );

-- Align the starter's unnamed unique constraint with the naming convention.
ALTER TABLE accounts
  RENAME CONSTRAINT accounts_tenant_id_domain_key TO uq_accounts_tenant_id_domain;

CREATE INDEX ix_accounts_tenant_id_name ON accounts (tenant_id, name);
