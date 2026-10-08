-- Migration 0034: connector contract checks (cross-cutting X2; expand only). Spec
-- docs/crosscutting/CONFORMANCE.md CX1 to CX3, D-88. down_revision 0033.
--
-- contract_fingerprints: insert-only. The schema fingerprint of each operation's
--   recorded fixture; the newest row per (provider_code, operation) is the active
--   contract. Shapes and enum sets are stored for diagnosis.
-- contract_drift_events: insert-only. One row per drifted live response: severity,
--   the structured changes, the payload hash and (internal only) the payload itself.
-- contract_operation_status: small state table; breaking drift disables the
--   operation fail closed (conditional UPDATE ... WHERE), an operator re-enables it
--   after recording a new fixture.
-- Platform tables (no tenant): drift is a property of the provider, not a tenant.

CREATE TABLE contract_fingerprints (
   id            bigserial NOT NULL,
   provider_code text NOT NULL,
   operation     text NOT NULL,
   fingerprint   text NOT NULL,
   shape         jsonb NOT NULL,
   enum_sets     jsonb NOT NULL DEFAULT '{}'::jsonb,
   source        text NOT NULL DEFAULT 'fixture',
   recorded_at   timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_contract_fingerprints PRIMARY KEY (id),
   CONSTRAINT ck_contract_fingerprints_shape CHECK (jsonb_typeof(shape) = 'object'),
   CONSTRAINT ck_contract_fingerprints_enums CHECK (jsonb_typeof(enum_sets) = 'object'),
   CONSTRAINT ck_contract_fingerprints_neutral CHECK (provider_code ~ '^p_[a-z_]+$')
);
CREATE INDEX ix_contract_fingerprints_op ON contract_fingerprints (provider_code, operation, recorded_at DESC);

CREATE TABLE contract_drift_events (
   id             bigserial NOT NULL,
   provider_code  text NOT NULL,
   operation      text NOT NULL,
   severity       text NOT NULL CHECK (severity IN ('additive', 'breaking')),
   changes        jsonb NOT NULL,
   payload_sha256 text NOT NULL,
   payload        jsonb,
   seen_at        timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_contract_drift_events PRIMARY KEY (id),
   CONSTRAINT ck_contract_drift_events_changes CHECK (jsonb_typeof(changes) = 'array')
);
CREATE INDEX ix_contract_drift_events_op ON contract_drift_events (provider_code, operation, seen_at DESC);

CREATE TABLE contract_operation_status (
   provider_code   text NOT NULL,
   operation       text NOT NULL,
   disabled        boolean NOT NULL DEFAULT false,
   disabled_at     timestamptz,
   disabled_reason text,
   updated_at      timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_contract_operation_status PRIMARY KEY (provider_code, operation)
);

-- app_rw may insert and read; nothing is updated or deleted (insert-only), except the
-- conditional status change on contract_operation_status.
GRANT SELECT, INSERT ON contract_fingerprints, contract_drift_events TO app_rw;
GRANT USAGE ON SEQUENCE contract_fingerprints_id_seq, contract_drift_events_id_seq TO app_rw;
GRANT SELECT, INSERT, UPDATE ON contract_operation_status TO app_rw;
REVOKE UPDATE, DELETE ON contract_fingerprints, contract_drift_events FROM app_rw;
