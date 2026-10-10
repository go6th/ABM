-- Remove first-party identity functions, policies and storage in dependency order.

DROP FUNCTION IF EXISTS local_auth_success(uuid, timestamptz);
DROP FUNCTION IF EXISTS local_auth_failure(text, timestamptz);
DROP FUNCTION IF EXISTS local_auth_memberships(uuid);
DROP FUNCTION IF EXISTS local_auth_lookup(text);
DROP FUNCTION IF EXISTS local_auth_verify_email(text, timestamptz);
DROP FUNCTION IF EXISTS local_auth_issue_token(uuid, text, text, timestamptz, timestamptz);
DROP FUNCTION IF EXISTS local_auth_create(text, text, timestamptz);

DROP POLICY IF EXISTS identity_authenticator_memberships ON memberships;
DROP POLICY IF EXISTS identity_authenticator_users ON users;
DROP POLICY IF EXISTS identity_authenticator_tenant_events ON tenant_events;
DROP POLICY IF EXISTS identity_authenticator_insert ON local_auth_events;
DROP POLICY IF EXISTS identity_authenticator_tokens ON local_auth_tokens;
DROP POLICY IF EXISTS identity_authenticator_access ON local_identities;

DROP TABLE IF EXISTS local_auth_events;
DROP TABLE IF EXISTS local_auth_tokens;
DROP TABLE IF EXISTS local_identities;

REVOKE SELECT (tenant_id, user_id, role, status) ON memberships FROM identity_authenticator;
REVOKE SELECT (tenant_id, subject, email) ON users FROM identity_authenticator;
REVOKE SELECT (tenant_id, seq, event) ON tenant_events FROM identity_authenticator;
DROP ROLE IF EXISTS identity_authenticator;
