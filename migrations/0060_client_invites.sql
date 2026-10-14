-- Add client (admin console A2): the operator creates a client and its first user, and copies an
-- invite link (nothing is sent automatically). The link carries a single-use invite token (purpose
-- 'invite_acceptance', at most 24 hours, issued by local_auth_issue_token); opening it lets the
-- invited person choose a password. The operator delivered the link to the address they entered,
-- so accepting it also marks that address verified.

-- reviewed: SECURITY DEFINER (owner identity_authenticator, NOLOGIN, NOBYPASSRLS, search_path
-- pinned, EXECUTE for app_rw only). Returns the email of the one identity an unexpired, unconsumed
-- invite token (given as its SHA-256 hash) belongs to, for the password policy's context terms;
-- identities are platform rows, not tenant rows.
CREATE FUNCTION local_auth_invite_email(p_token_hash text, p_now timestamptz)
   RETURNS text
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT u.email
     FROM local_auth_tokens t
     JOIN local_identities u ON u.id = t.user_id
    WHERE t.token_hash = p_token_hash AND t.purpose = 'invite_acceptance'
      AND t.consumed_at IS NULL AND t.expires_at > p_now
      AND u.status IN ('pending_verification', 'active')
$$;

-- reviewed: SECURITY DEFINER (owner identity_authenticator, as above). Consumes the single-use
-- invite token and, for that one identity only, sets the chosen password hash, activates it and
-- marks the email verified, in one transaction; refuses malformed hashes and disabled identities;
-- writes no tenant row and reads no tenant data.
CREATE FUNCTION local_auth_accept_invite(
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
    WHERE t.token_hash = p_token_hash AND t.purpose = 'invite_acceptance'
      AND t.consumed_at IS NULL AND t.expires_at > p_now
      AND EXISTS (SELECT 1 FROM local_identities u WHERE u.id = t.user_id
         AND u.status IN ('pending_verification', 'active'))
   RETURNING t.user_id INTO identity_id;
   IF identity_id IS NULL THEN
      RETURN NULL;
   END IF;
   UPDATE local_identities u
      SET password_hash = p_password_hash, password_changed_at = p_now, status = 'active',
          email_verified_at = COALESCE(u.email_verified_at, p_now),
          failed_logins = 0, locked_until = NULL, updated_at = p_now
    WHERE u.id = identity_id AND u.status IN ('pending_verification', 'active');
   IF NOT FOUND THEN
      RETURN NULL;
   END IF;
   INSERT INTO local_auth_events (user_id, event, reason_code, at)
   VALUES (identity_id, 'email_verified', 'invite_accepted', p_now),
          (identity_id, 'password_changed', 'invite_accepted', p_now);
   RETURN identity_id;
END $$;

ALTER FUNCTION local_auth_invite_email(text, timestamptz) OWNER TO identity_authenticator;
ALTER FUNCTION local_auth_accept_invite(text, text, timestamptz) OWNER TO identity_authenticator;
REVOKE ALL ON FUNCTION local_auth_invite_email(text, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION local_auth_accept_invite(text, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION local_auth_invite_email(text, timestamptz) TO app_rw;
GRANT EXECUTE ON FUNCTION local_auth_accept_invite(text, text, timestamptz) TO app_rw;
