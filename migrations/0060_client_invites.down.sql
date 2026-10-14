-- Remove the invite acceptance functions.
DROP FUNCTION IF EXISTS local_auth_accept_invite(text, text, timestamptz);
DROP FUNCTION IF EXISTS local_auth_invite_email(text, timestamptz);
