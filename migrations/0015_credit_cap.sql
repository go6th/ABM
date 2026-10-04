-- Migration 0015: credit cap v1 (expand only; no existing column dropped or altered).
-- Spec docs/prompts/build_credit_cap.md section 6. down_revision 0014 (people pipeline).
--
-- Table naming (ADR-0026): the workflow, the connector reserve and settlement all use
-- account_runs.budget_usd and spent_usd; runs.credit_cap (0004, integer) is the data-layer mirror
-- and is not used for enforcement. No third cap column is created: the approved cap is written to
-- account_runs.budget_usd and the version that set it to account_runs.cap_version.
--
-- account_runs gains:
--   cap_required      true for every run created from now on (fail closed: no approved cap, no
--                     paid call); rows that exist before this migration keep false (legacy rule)
--   cap_version       the approved run_cap_versions.version the enforcement columns came from
--   cap_sample_ops    sample calls allowed per unknown-cost operation by the approved cap (K5)
--   attended          an operator is on the run (D-28 extension flow); unattended by default
--   cap_overshoot_usd actual cost above a call's declared upper bound (K4); never clamped
--   unreconciled_usd  stale reserves counted but not yet reconciled (K9)
-- and CHECK spent_usd <= budget_usd + cap_overshoot_usd (added NOT VALID, then validated).
--
-- Insert-only tables (INSERT and SELECT for app_rw, no UPDATE, no DELETE): run_cap_versions,
-- run_cap_events, balance_snapshots, spend_reports, prepulled_files. Forced RLS TO app_rw on all.
-- One decision per cap version is enforced by a partial unique index (double approval, approve
-- versus expire races). The tenant view of spend_reports is readable by public_reader through a
-- column grant that excludes operator_json.
-- Rollback: 0015_credit_cap.down.sql.

ALTER TABLE account_runs
   ADD COLUMN cap_required      boolean NOT NULL DEFAULT false,
   ADD COLUMN cap_version       integer,
   ADD COLUMN cap_sample_ops    jsonb NOT NULL DEFAULT '{}'::jsonb,
   ADD COLUMN attended          boolean NOT NULL DEFAULT false,
   ADD COLUMN cap_overshoot_usd numeric(14,6) NOT NULL DEFAULT 0,
   ADD COLUMN unreconciled_usd  numeric(14,6) NOT NULL DEFAULT 0,
   ADD CONSTRAINT ck_account_runs_cap_version CHECK (cap_version IS NULL OR cap_version >= 1),
   ADD CONSTRAINT ck_account_runs_overshoot CHECK (cap_overshoot_usd >= 0),
   ADD CONSTRAINT ck_account_runs_unreconciled CHECK (unreconciled_usd >= 0),
   ADD CONSTRAINT ck_account_runs_sample_ops CHECK (jsonb_typeof(cap_sample_ops) = 'object'),
   ADD CONSTRAINT ck_account_runs_capped_has_budget CHECK (cap_version IS NULL OR budget_usd IS NOT NULL),
   ADD CONSTRAINT ck_account_runs_spent_within_cap
      CHECK (budget_usd IS NULL OR spent_usd <= budget_usd + cap_overshoot_usd) NOT VALID;
ALTER TABLE account_runs VALIDATE CONSTRAINT ck_account_runs_spent_within_cap;
-- existing rows keep false (legacy); every row inserted from now on requires a cap
ALTER TABLE account_runs ALTER COLUMN cap_required SET DEFAULT true;

CREATE TABLE run_cap_versions (
   id            bigserial NOT NULL,
   tenant_id     uuid NOT NULL DEFAULT app_tenant(),
   run_id        uuid NOT NULL,
   version       integer NOT NULL,
   cap_usd       numeric(12,6) NOT NULL,
   proposal_json jsonb NOT NULL,
   proposal_hash text NOT NULL,
   config_hash   text NOT NULL,
   created_at    timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_run_cap_versions PRIMARY KEY (id),
   CONSTRAINT fk_run_cap_versions_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT uq_run_cap_versions_run_version UNIQUE (run_id, version),
   CONSTRAINT ck_run_cap_versions_cap CHECK (cap_usd > 0),
   CONSTRAINT ck_run_cap_versions_version CHECK (version >= 1),
   CONSTRAINT ck_run_cap_versions_hash CHECK (proposal_hash ~ '^[0-9a-f]{64}$' AND config_hash ~ '^[0-9a-f]{64}$')
);
ALTER TABLE run_cap_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_cap_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON run_cap_versions TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON run_cap_versions TO app_rw;
GRANT USAGE ON SEQUENCE run_cap_versions_id_seq TO app_rw;

CREATE TABLE run_cap_events (
   id         bigserial NOT NULL,
   tenant_id  uuid NOT NULL DEFAULT app_tenant(),
   run_id     uuid NOT NULL,
   version    integer NOT NULL,
   event      text NOT NULL,
   principal  text,
   reason     text,
   at         timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_run_cap_events PRIMARY KEY (id),
   CONSTRAINT fk_run_cap_events_version FOREIGN KEY (run_id, version)
      REFERENCES run_cap_versions (run_id, version),
   CONSTRAINT ck_run_cap_events_event CHECK (event IN ('proposed', 'approved', 'rejected',
      'expired', 'superseded', 'extension_requested', 'extension_declined')),
   CONSTRAINT ck_run_cap_events_principal CHECK (event NOT IN ('approved', 'rejected')
      OR principal IS NOT NULL)
);
-- exactly one decision per version: a second approval, or an approval racing an expiry, fails
CREATE UNIQUE INDEX uq_run_cap_events_decision ON run_cap_events (run_id, version)
   WHERE event IN ('approved', 'rejected', 'expired', 'extension_declined');
-- a version is superseded at most once (when a later version is approved)
CREATE UNIQUE INDEX uq_run_cap_events_superseded ON run_cap_events (run_id, version)
   WHERE event = 'superseded';
CREATE UNIQUE INDEX uq_run_cap_events_proposed ON run_cap_events (run_id, version)
   WHERE event IN ('proposed', 'extension_requested');
ALTER TABLE run_cap_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_cap_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON run_cap_events TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON run_cap_events TO app_rw;
GRANT USAGE ON SEQUENCE run_cap_events_id_seq TO app_rw;

CREATE TABLE balance_snapshots (
   id            bigserial NOT NULL,
   tenant_id     uuid NOT NULL DEFAULT app_tenant(),
   run_id        uuid NOT NULL,
   phase         text NOT NULL,
   provider_code text NOT NULL,
   status        text NOT NULL,
   balance       numeric(18,4),
   unit          text,
   source        text NOT NULL,
   taken_at      timestamptz NOT NULL,
   CONSTRAINT pk_balance_snapshots PRIMARY KEY (id),
   CONSTRAINT fk_balance_snapshots_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_balance_snapshots_status CHECK (status IN ('ok', 'low', 'empty', 'unknown')),
   CONSTRAINT ck_balance_snapshots_phase CHECK (phase IN ('start', 'end')),
   CONSTRAINT ck_balance_snapshots_provider CHECK (provider_code ~ '^p_[a-z]+$'),
   CONSTRAINT ck_balance_snapshots_known CHECK (status = 'unknown' OR balance IS NOT NULL OR status = 'empty')
);
ALTER TABLE balance_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE balance_snapshots FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON balance_snapshots TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON balance_snapshots TO app_rw;
GRANT USAGE ON SEQUENCE balance_snapshots_id_seq TO app_rw;

CREATE TABLE spend_reports (
   id            bigserial NOT NULL,
   tenant_id     uuid NOT NULL DEFAULT app_tenant(),
   run_id        uuid NOT NULL,
   version       integer NOT NULL,
   status        text NOT NULL,
   content_hash  text NOT NULL,
   operator_json jsonb NOT NULL,   -- internal: provider codes, amounts; never a client file
   tenant_json   jsonb NOT NULL,   -- the allow-listed projection (D-30)
   created_at    timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_spend_reports PRIMARY KEY (id),
   CONSTRAINT fk_spend_reports_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT uq_spend_reports_run_version UNIQUE (run_id, version),
   CONSTRAINT ck_spend_reports_status CHECK (status IN ('reconciled', 'reconciliation_failed')),
   CONSTRAINT ck_spend_reports_hash CHECK (content_hash ~ '^[0-9a-f]{64}$')
);
ALTER TABLE spend_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE spend_reports FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON spend_reports TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
CREATE POLICY tenant_view ON spend_reports FOR SELECT TO public_reader
   USING (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON spend_reports TO app_rw;
GRANT SELECT (tenant_id, run_id, version, status, tenant_json, created_at) ON spend_reports TO public_reader;
GRANT USAGE ON SEQUENCE spend_reports_id_seq TO app_rw;

CREATE TABLE prepulled_files (
   id            bigserial NOT NULL,
   tenant_id     uuid NOT NULL DEFAULT app_tenant(),
   run_id        uuid NOT NULL,
   provider_code text NOT NULL,
   operation     text NOT NULL,
   sha256        text NOT NULL,
   supplied_by   text NOT NULL,
   object_ref    text NOT NULL,
   record_count  integer NOT NULL,
   registered_at timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_prepulled_files PRIMARY KEY (id),
   CONSTRAINT fk_prepulled_files_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT uq_prepulled_files_run_op UNIQUE (run_id, provider_code, operation),
   CONSTRAINT ck_prepulled_files_sha CHECK (sha256 ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_prepulled_files_provider CHECK (provider_code ~ '^p_[a-z]+$'),
   CONSTRAINT ck_prepulled_files_count CHECK (record_count >= 0)
);
ALTER TABLE prepulled_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE prepulled_files FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON prepulled_files TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON prepulled_files TO app_rw;
GRANT USAGE ON SEQUENCE prepulled_files_id_seq TO app_rw;

-- Spend by provider derived from the insert-only ledger events (the same definition as the
-- connector's ledger_sum). security_invoker: the caller's RLS applies.
CREATE VIEW run_spend_by_provider WITH (security_invoker = true) AS
SELECT e.tenant_id,
       e.run_id,
       e.provider_code,
       e.operation,
       COUNT(*) FILTER (WHERE e.event = 'done') AS done_calls,
       COUNT(*) FILTER (WHERE e.event = 'failed' AND COALESCE(e.charged_usd, 0) > 0) AS failed_after_charge,
       COALESCE(SUM(e.charged_usd) FILTER (WHERE e.event IN ('done', 'failed', 'abandoned')), 0)
          AS charged_usd,
       COALESCE(SUM(e.credits) FILTER (WHERE e.event = 'done'), 0) AS credits
  FROM tool_call_events e
 GROUP BY e.tenant_id, e.run_id, e.provider_code, e.operation;
GRANT SELECT ON run_spend_by_provider TO app_rw;

-- The nightly spend invariant job needs the runs to check across tenants. It gets identifiers
-- only (no amount, no name), through this narrow function; each run is then read under its own
-- tenant context (tenant_txn), so RLS still applies to every figure.
-- reviewed: SECURITY DEFINER returns (tenant_id, run_id) identifiers only, of runs created since
-- the given time; search_path pinned; EXECUTE granted to app_rw only.
CREATE FUNCTION spend_runs_to_check(since timestamptz)
   RETURNS TABLE (tenant_id uuid, run_id uuid)
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT r.tenant_id, r.id FROM account_runs r WHERE r.created_at >= since ORDER BY r.created_at, r.id
$$;
REVOKE ALL ON FUNCTION spend_runs_to_check(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION spend_runs_to_check(timestamptz) TO app_rw;

