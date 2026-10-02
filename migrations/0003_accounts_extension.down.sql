-- migrations/0003_accounts_extension.down.sql
DROP INDEX IF EXISTS ix_accounts_tenant_id_name;
ALTER TABLE accounts DROP CONSTRAINT IF EXISTS ck_accounts_domain_shape;
ALTER TABLE accounts
  DROP COLUMN IF EXISTS pursuing_client,
  DROP COLUMN IF EXISTS primary_lane,
  DROP COLUMN IF EXISTS industry,
  DROP COLUMN IF EXISTS name,
  DROP COLUMN IF EXISTS created_at,
  DROP COLUMN IF EXISTS updated_at;
ALTER TABLE accounts RENAME COLUMN careers_url_verified_at TO careers_verified_at;
ALTER TABLE accounts
  RENAME CONSTRAINT uq_accounts_tenant_id_domain TO accounts_tenant_id_domain_key;
