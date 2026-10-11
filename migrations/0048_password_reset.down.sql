-- Remove the password reset functions and the reset grant on local identities.
DROP FUNCTION IF EXISTS local_auth_reset_password(text, text, timestamptz);
DROP FUNCTION IF EXISTS local_auth_reset_email(text, timestamptz);
REVOKE UPDATE (password_hash, password_changed_at)
   ON local_identities FROM identity_authenticator;
