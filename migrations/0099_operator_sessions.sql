-- Migration 0099 (2026-11-03): opaque staff sessions (fix prompt B, task 3; ADR-0111).
--
-- The operator console kept the raw Google ID token as its session cookie. Now the console trades
-- a verified staff IdP token (fresh MFA) once for a random 256-bit token; this table keeps only
-- its SHA-256. Staff plane, no tenant (no row level security: nothing here is a tenant's). A
-- session ends after idle_timeout_s without a request, at expires_at (sign-in + the absolute
-- timeout of config/session.yaml), or when revoked (sign-out). Rows are kept for audit.
-- Rollback: 0099_operator_sessions.down.sql (drops the table; signed-in staff sign in again).

CREATE TABLE operator_sessions (
   id           uuid NOT NULL DEFAULT gen_random_uuid(),
   token_hash   bytea NOT NULL,
   staff_id     text NOT NULL,
   role         text NOT NULL,
   step_up_at   timestamptz,
   created_at   timestamptz NOT NULL,
   last_seen_at timestamptz NOT NULL,
   expires_at   timestamptz NOT NULL,
   revoked_at   timestamptz,
   CONSTRAINT pk_operator_sessions PRIMARY KEY (id),
   CONSTRAINT uq_operator_sessions_token_hash UNIQUE (token_hash),
   CONSTRAINT ck_operator_sessions_hash CHECK (octet_length(token_hash) = 32),
   CONSTRAINT ck_operator_sessions_role CHECK (role IN ('operator', 'support')),
   CONSTRAINT ck_operator_sessions_staff CHECK (char_length(staff_id) BETWEEN 1 AND 320),
   CONSTRAINT ck_operator_sessions_window CHECK (expires_at > created_at AND last_seen_at >= created_at)
);
CREATE INDEX ix_operator_sessions_staff ON operator_sessions (staff_id, created_at DESC);
GRANT SELECT, INSERT, UPDATE ON operator_sessions TO app_rw;
