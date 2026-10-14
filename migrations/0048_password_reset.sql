-- Add local password recovery without expanding direct app_rw access to credential tables.
GRANT UPDATE (password_hash, password_changed_at) ON local_identities TO identity_authenticator;

-- reviewed: SECURITY DEFINER (owner identity_authenticator, NOLOGIN, NOBYPASSRLS, search_path
-- pinned, EXECUTE for app_rw only). Returns one active, verified identity's email for an unexpired,
-- unconsumed reset token given as its SHA-256 hash; identities are platform rows, not tenant rows.
CREATE FUNCTION local_auth_reset_email(p_token_hash text, p_now timestamptz)
   RETURNS text
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT u.email
     FROM local_auth_tokens t
     JOIN local_identities u ON u.id = t.user_id
    WHERE t.token_hash = p_token_hash AND t.purpose = 'password_reset'
      AND t.consumed_at IS NULL AND t.expires_at > p_now
      AND u.status = 'active' AND u.email_verified_at IS NOT NULL
$$;

-- reviewed: SECURITY DEFINER (owner identity_authenticator, as above). Consumes the single-use
-- reset token and sets that one identity's password hash in one transaction; refuses malformed
-- hashes; writes no tenant row and reads no tenant data.
CREATE FUNCTION local_auth_reset_password(
   p_token_hash text, p_password_hash text, p_now timestamptz)
   RETURNS uuid
   LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
   identity_id uuid;
BEGIN
   IF p_token_hash !~ '^[0-9a-f]{64}$' OR p_password_hash NOT LIKE '$argon2id$%'
      OR char_length(p_password_hash) > 512 THEN
      RETURN NULL;
   END IF;
   UPDATE local_auth_tokens t SET consumed_at = p_now
    WHERE t.token_hash = p_token_hash AND t.purpose = 'password_reset'
      AND t.consumed_at IS NULL AND t.expires_at > p_now
      AND EXISTS (SELECT 1 FROM local_identities u WHERE u.id = t.user_id
         AND u.status = 'active' AND u.email_verified_at IS NOT NULL)
   RETURNING t.user_id INTO identity_id;
   IF identity_id IS NULL THEN
      RETURN NULL;
   END IF;
   UPDATE local_identities u
      SET password_hash = p_password_hash, password_changed_at = p_now,
          failed_logins = 0, locked_until = NULL, updated_at = p_now
    WHERE u.id = identity_id AND u.status = 'active' AND u.email_verified_at IS NOT NULL;
   IF NOT FOUND THEN
      RETURN NULL;
   END IF;
   INSERT INTO local_auth_events (user_id, event, at)
   VALUES (identity_id, 'password_reset_completed', p_now);
   RETURN identity_id;
END $$;

ALTER FUNCTION local_auth_reset_email(text, timestamptz) OWNER TO identity_authenticator;
ALTER FUNCTION local_auth_reset_password(text, text, timestamptz) OWNER TO identity_authenticator;
REVOKE ALL ON FUNCTION local_auth_reset_email(text, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION local_auth_reset_password(text, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION local_auth_reset_email(text, timestamptz) TO app_rw;
GRANT EXECUTE ON FUNCTION local_auth_reset_password(text, text, timestamptz) TO app_rw;
