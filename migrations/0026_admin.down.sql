-- Rollback of 0026_admin.sql (development only; production never contracts in the same release).
DROP FUNCTION IF EXISTS provision_tenant(text);
DROP POLICY IF EXISTS tenant_provision ON tenants;
DROP POLICY IF EXISTS tenant_provision_read ON tenants;
DROP POLICY IF EXISTS tenant_provision ON wallets;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tenant_provisioner') THEN
    REVOKE ALL ON tenants, wallets FROM tenant_provisioner;
  END IF;
END $$;
DROP FUNCTION IF EXISTS operator_tenant_overview();
DROP FUNCTION IF EXISTS support_grant_tenant(uuid, text, timestamptz);
DROP POLICY IF EXISTS operator_overview ON tenants;
DROP POLICY IF EXISTS operator_overview ON memberships;
DROP POLICY IF EXISTS operator_overview ON accounts;
DROP POLICY IF EXISTS operator_overview ON account_runs;
DROP POLICY IF EXISTS operator_overview ON tenant_events;
REVOKE ALL ON tenants, memberships, accounts, account_runs, tenant_events FROM operator_resolver;
DROP TABLE IF EXISTS operator_audit;
DROP TABLE IF EXISTS broker_keys;
DROP TABLE IF EXISTS flag_events;
DROP TABLE IF EXISTS feature_flags;
DROP TABLE IF EXISTS quota_versions;
DROP TABLE IF EXISTS support_session_events;
DROP TABLE IF EXISTS support_grants;
