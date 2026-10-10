-- First-party credentials are global identities, separate from the tenant-scoped users rows.
-- The application role has no direct table access; only these narrow functions may cross the
-- identity boundary. All resolver roles are NOLOGIN and NOBYPASSRLS.

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'identity_authenticator') THEN
    CREATE ROLE identity_authenticator NOLOGIN NOBYPASSRLS;
  END IF;
END $$;

CREATE TABLE local_identities (
   id                     uuid NOT NULL DEFAULT gen_random_uuid(),
   email                  text NOT NULL,
   password_hash          text NOT NULL,
   status                 text NOT NULL DEFAULT 'pending_verification',
   email_verified_at      timestamptz,
   failed_logins          integer NOT NULL DEFAULT 0,
   locked_until           timestamptz,
   last_login_at          timestamptz,
   password_changed_at    timestamptz NOT NULL,
   mfa_secret_ciphertext  bytea,
   mfa_enabled            boolean NOT NULL DEFAULT false,
   created_at             timestamptz NOT NULL,
   updated_at             timestamptz NOT NULL,
   CONSTRAINT pk_local_identities PRIMARY KEY (id),
   CONSTRAINT uq_local_identities_email UNIQUE (email),
   CONSTRAINT ck_local_identities_email CHECK (
      email = lower(btrim(email)) AND char_length(email) BETWEEN 3 AND 320
      AND email ~ '^[^@[:space:]]+@[^@[:space:]]+$'),
   CONSTRAINT ck_local_identities_hash CHECK (
      password_hash LIKE '$argon2id$%' AND char_length(password_hash) <= 512),
   CONSTRAINT ck_local_identities_status CHECK (
      status IN ('pending_verification', 'active', 'disabled')),
   CONSTRAINT ck_local_identities_failures CHECK (failed_logins BETWEEN 0 AND 1000),
   CONSTRAINT ck_local_identities_mfa CHECK (
      (mfa_enabled = false) OR (mfa_secret_ciphertext IS NOT NULL
         AND octet_length(mfa_secret_ciphertext) >= 28)),
   CONSTRAINT ck_local_identities_dates CHECK (updated_at >= created_at)
);
ALTER TABLE local_identities ENABLE ROW LEVEL SECURITY;
ALTER TABLE local_identities FORCE ROW LEVEL SECURITY;
CREATE POLICY identity_authenticator_access ON local_identities TO identity_authenticator
   USING (true) WITH CHECK (true);
GRANT SELECT (id, email, password_hash, status, email_verified_at, failed_logins,
   locked_until, mfa_secret_ciphertext, mfa_enabled) ON local_identities TO identity_authenticator;
GRANT INSERT (id, email, password_hash, status, password_changed_at, created_at, updated_at)
   ON local_identities TO identity_authenticator;
GRANT UPDATE (failed_logins, locked_until, last_login_at, updated_at, status, email_verified_at)
   ON local_identities TO identity_authenticator;

CREATE TABLE local_auth_tokens (
   id          uuid NOT NULL DEFAULT gen_random_uuid(),
   user_id     uuid NOT NULL,
   purpose     text NOT NULL,
   token_hash  text NOT NULL,
   expires_at  timestamptz NOT NULL,
   consumed_at timestamptz,
   created_at  timestamptz NOT NULL,
   CONSTRAINT pk_local_auth_tokens PRIMARY KEY (id),
   CONSTRAINT fk_local_auth_tokens_user FOREIGN KEY (user_id) REFERENCES local_identities (id),
   CONSTRAINT uq_local_auth_tokens_hash UNIQUE (token_hash),
   CONSTRAINT ck_local_auth_tokens_purpose CHECK (
      purpose IN ('email_verification', 'password_reset', 'invite_acceptance')),
   CONSTRAINT ck_local_auth_tokens_hash CHECK (token_hash ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_local_auth_tokens_window CHECK (expires_at > created_at),
   CONSTRAINT ck_local_auth_tokens_consumed CHECK (consumed_at IS NULL OR consumed_at >= created_at)
);
CREATE INDEX ix_local_auth_tokens_user ON local_auth_tokens (user_id, purpose, expires_at)
   WHERE consumed_at IS NULL;
ALTER TABLE local_auth_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE local_auth_tokens FORCE ROW LEVEL SECURITY;
CREATE POLICY identity_authenticator_tokens ON local_auth_tokens TO identity_authenticator
   USING (true) WITH CHECK (true);
GRANT SELECT, INSERT ON local_auth_tokens TO identity_authenticator;
GRANT UPDATE (consumed_at) ON local_auth_tokens TO identity_authenticator;

CREATE TABLE local_auth_events (
   id          bigserial NOT NULL,
   user_id     uuid,
   event       text NOT NULL,
   reason_code text,
   request_id  uuid,
   at          timestamptz NOT NULL,
   CONSTRAINT pk_local_auth_events PRIMARY KEY (id),
   CONSTRAINT fk_local_auth_events_user FOREIGN KEY (user_id) REFERENCES local_identities (id),
   CONSTRAINT ck_local_auth_events_event CHECK (event IN (
      'account_registered', 'password_enrollment_started', 'login_succeeded', 'login_failed', 'login_locked',
      'email_verified', 'password_changed', 'password_reset_requested',
      'password_reset_completed', 'mfa_enrolled', 'mfa_disabled', 'refresh_reuse')),
   CONSTRAINT ck_local_auth_events_reason CHECK (
      reason_code IS NULL OR reason_code ~ '^[a-z_]{1,40}$')
);
ALTER TABLE local_auth_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE local_auth_events FORCE ROW LEVEL SECURITY;
CREATE POLICY identity_authenticator_insert ON local_auth_events FOR INSERT
   TO identity_authenticator WITH CHECK (true);
GRANT INSERT ON local_auth_events TO identity_authenticator;
GRANT USAGE ON SEQUENCE local_auth_events_id_seq TO identity_authenticator;

-- Membership resolution is read-only and returns tenant ids and roles only. Tenant routes still
-- run the normal MembershipCache check after a server-side session has been established.
CREATE POLICY identity_authenticator_memberships ON memberships FOR SELECT
   TO identity_authenticator USING (true);
GRANT SELECT (tenant_id, user_id, role, status) ON memberships TO identity_authenticator;
CREATE POLICY identity_authenticator_users ON users FOR SELECT
   TO identity_authenticator USING (true);
GRANT SELECT (tenant_id, subject, email) ON users TO identity_authenticator;
CREATE POLICY identity_authenticator_tenant_events ON tenant_events FOR SELECT
   TO identity_authenticator USING (true);
GRANT SELECT (tenant_id, seq, event) ON tenant_events TO identity_authenticator;

-- reviewed: SECURITY DEFINER (owner identity_authenticator, NOLOGIN, NOBYPASSRLS, explicit
-- column grants only). Returns a single normalized email's credential fields to the API service.
CREATE FUNCTION local_auth_create(p_email text, p_password_hash text, p_now timestamptz)
   RETURNS uuid
   LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
   new_id uuid;
BEGIN
   INSERT INTO local_identities (email, password_hash, status, password_changed_at,
      created_at, updated_at)
   VALUES (lower(btrim(p_email)), p_password_hash, 'pending_verification', p_now, p_now, p_now)
   RETURNING id INTO new_id;
   INSERT INTO local_auth_events (user_id, event, at)
   VALUES (new_id, 'password_enrollment_started', p_now);
   RETURN new_id;
END $$;

-- reviewed: SECURITY DEFINER (owner identity_authenticator). Verification links are single-use,
-- short-lived and stored only as SHA-256 hashes; email becomes verified in the same transaction.
CREATE FUNCTION local_auth_verify_email(p_token_hash text, p_now timestamptz)
   RETURNS uuid
   LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
   identity_id uuid;
BEGIN
   UPDATE local_auth_tokens t SET consumed_at = p_now
    WHERE t.token_hash = p_token_hash AND t.purpose = 'email_verification'
      AND t.consumed_at IS NULL AND t.expires_at > p_now
   RETURNING t.user_id INTO identity_id;
   IF identity_id IS NULL THEN
      RETURN NULL;
   END IF;
   UPDATE local_identities u SET status = 'active', email_verified_at = p_now, updated_at = p_now
    WHERE u.id = identity_id AND u.status = 'pending_verification';
   IF NOT FOUND THEN
      RETURN NULL;
   END IF;
   INSERT INTO local_auth_events (user_id, event, at)
   VALUES (identity_id, 'email_verified', p_now);
   RETURN identity_id;
END $$;

-- reviewed: SECURITY DEFINER (owner identity_authenticator). Replaces any outstanding token for
-- this user and purpose; the application stores and delivers the raw token, never the database.
CREATE FUNCTION local_auth_issue_token(
   p_user_id uuid, p_purpose text, p_token_hash text, p_expires_at timestamptz, p_now timestamptz)
   RETURNS boolean
   LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
   IF p_user_id IS NULL OR p_now IS NULL OR p_purpose IS NULL OR p_token_hash IS NULL
      OR p_expires_at IS NULL
      OR p_purpose NOT IN ('email_verification', 'password_reset', 'invite_acceptance')
      OR p_expires_at <= p_now OR p_token_hash !~ '^[0-9a-f]{64}$'
      OR (p_purpose = 'password_reset' AND p_expires_at > p_now + interval '1 hour')
      OR (p_purpose <> 'password_reset' AND p_expires_at > p_now + interval '24 hours') THEN
      RETURN false;
   END IF;
   IF NOT EXISTS (SELECT 1 FROM local_identities u WHERE u.id = p_user_id
      AND ((p_purpose = 'email_verification' AND u.status = 'pending_verification')
        OR (p_purpose = 'password_reset' AND u.status = 'active')
        OR p_purpose = 'invite_acceptance')) THEN
      RETURN false;
   END IF;
   UPDATE local_auth_tokens t SET consumed_at = GREATEST(p_now, t.created_at)
    WHERE t.user_id = p_user_id AND t.purpose = p_purpose AND t.consumed_at IS NULL;
   INSERT INTO local_auth_tokens (user_id, purpose, token_hash, expires_at, created_at)
   VALUES (p_user_id, p_purpose, p_token_hash, p_expires_at, p_now);
   IF p_purpose = 'password_reset' THEN
      INSERT INTO local_auth_events (user_id, event, at)
      VALUES (p_user_id, 'password_reset_requested', p_now);
   END IF;
   RETURN true;
END $$;

CREATE FUNCTION local_auth_lookup(p_email text)
   RETURNS TABLE (
      user_id uuid,
      password_hash text,
      status text,
      email_verified_at timestamptz,
      failed_logins integer,
      locked_until timestamptz,
      mfa_secret_ciphertext bytea,
      mfa_enabled boolean)
   -- reviewed: SECURITY DEFINER (owner identity_authenticator, NOLOGIN, NOBYPASSRLS, explicit
   -- column grants only). Returns this email's credential fields to the backend authenticator.
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT u.id, u.password_hash, u.status, u.email_verified_at, u.failed_logins,
          u.locked_until, u.mfa_secret_ciphertext, u.mfa_enabled
     FROM local_identities u
    WHERE u.email = lower(btrim(p_email))
$$;

-- reviewed: SECURITY DEFINER (owner identity_authenticator). Returns active, non-suspended
-- memberships only; caller still performs the session membership check on every tenant request.
CREATE FUNCTION local_auth_memberships(p_user_id uuid)
   RETURNS TABLE (tenant_id uuid, user_id text, role text)
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT m.tenant_id, m.user_id, m.role
     FROM memberships m
     JOIN users tu ON tu.tenant_id = m.tenant_id AND tu.subject = m.user_id
     JOIN local_identities u ON u.id = p_user_id
    WHERE u.status = 'active' AND u.email_verified_at IS NOT NULL AND m.status = 'active'
      AND lower(btrim(tu.email)) = u.email
      AND NOT COALESCE((
         SELECT e.event = 'suspended' FROM tenant_events e
          WHERE e.tenant_id = m.tenant_id AND e.event IN ('suspended', 'reactivated')
          ORDER BY e.seq DESC LIMIT 1), false)
    ORDER BY m.tenant_id
$$;

-- reviewed: SECURITY DEFINER (owner identity_authenticator). Failure counters are updated
-- atomically. Progressive lockout thresholds are 5 attempts/1 minute, 8/5 minutes and 12/1 hour.
CREATE FUNCTION local_auth_failure(p_email text, p_now timestamptz)
   RETURNS TABLE (failed_logins integer, locked_until timestamptz)
   LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
   affected integer;
   identity_id uuid;
   lock_time timestamptz;
   failures integer;
BEGIN
   UPDATE local_identities u
      SET failed_logins = LEAST(u.failed_logins + 1, 1000),
          locked_until = CASE
             WHEN u.failed_logins + 1 >= 12 THEN p_now + interval '1 hour'
             WHEN u.failed_logins + 1 >= 8 THEN p_now + interval '5 minutes'
             WHEN u.failed_logins + 1 >= 5 THEN p_now + interval '1 minute'
             ELSE u.locked_until END,
          updated_at = p_now
    WHERE u.email = lower(btrim(p_email))
    RETURNING u.id, u.failed_logins, u.locked_until
         INTO identity_id, failures, lock_time;
   GET DIAGNOSTICS affected = ROW_COUNT;
   IF affected = 1 THEN
      INSERT INTO local_auth_events (user_id, event, reason_code, at)
      VALUES (identity_id, CASE WHEN lock_time > p_now THEN 'login_locked' ELSE 'login_failed' END,
         CASE WHEN lock_time > p_now THEN 'progressive_lockout' ELSE 'invalid_credentials' END,
         p_now);
      RETURN QUERY SELECT failures, lock_time;
   ELSE
      -- Record only a fixed reason and a null subject; unknown emails never enter auth storage.
      INSERT INTO local_auth_events (user_id, event, reason_code, at)
      VALUES (NULL, 'login_failed', 'invalid_credentials', p_now);
   END IF;
END $$;

-- reviewed: SECURITY DEFINER (owner identity_authenticator). Clear lockout after successful
-- credential verification and write the authentication event in the same transaction.
CREATE FUNCTION local_auth_success(p_user_id uuid, p_now timestamptz)
   RETURNS boolean
   LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
   affected integer;
BEGIN
   UPDATE local_identities u
      SET failed_logins = 0, locked_until = NULL, last_login_at = p_now, updated_at = p_now
    WHERE u.id = p_user_id AND u.status = 'active' AND u.email_verified_at IS NOT NULL
      AND (u.locked_until IS NULL OR u.locked_until <= p_now);
   GET DIAGNOSTICS affected = ROW_COUNT;
   IF affected = 1 THEN
      INSERT INTO local_auth_events (user_id, event, at) VALUES (p_user_id, 'login_succeeded', p_now);
   END IF;
   RETURN affected = 1;
END $$;

ALTER FUNCTION local_auth_lookup(text) OWNER TO identity_authenticator;
ALTER FUNCTION local_auth_create(text, text, timestamptz) OWNER TO identity_authenticator;
ALTER FUNCTION local_auth_verify_email(text, timestamptz) OWNER TO identity_authenticator;
ALTER FUNCTION local_auth_issue_token(uuid, text, text, timestamptz, timestamptz)
   OWNER TO identity_authenticator;
ALTER FUNCTION local_auth_memberships(uuid) OWNER TO identity_authenticator;
ALTER FUNCTION local_auth_failure(text, timestamptz) OWNER TO identity_authenticator;
ALTER FUNCTION local_auth_success(uuid, timestamptz) OWNER TO identity_authenticator;
REVOKE ALL ON FUNCTION local_auth_lookup(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION local_auth_create(text, text, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION local_auth_verify_email(text, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION local_auth_issue_token(uuid, text, text, timestamptz, timestamptz)
   FROM PUBLIC;
REVOKE ALL ON FUNCTION local_auth_memberships(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION local_auth_failure(text, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION local_auth_success(uuid, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION local_auth_lookup(text) TO app_rw;
GRANT EXECUTE ON FUNCTION local_auth_create(text, text, timestamptz) TO app_rw;
GRANT EXECUTE ON FUNCTION local_auth_verify_email(text, timestamptz) TO app_rw;
GRANT EXECUTE ON FUNCTION local_auth_issue_token(uuid, text, text, timestamptz, timestamptz)
   TO app_rw;
GRANT EXECUTE ON FUNCTION local_auth_memberships(uuid) TO app_rw;
GRANT EXECUTE ON FUNCTION local_auth_failure(text, timestamptz) TO app_rw;
GRANT EXECUTE ON FUNCTION local_auth_success(uuid, timestamptz) TO app_rw;
