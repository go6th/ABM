-- migrations/0001_core.sql
-- NOT in the Architecture document. The ARCH listings (0002_rls.sql, runs.py, graph.py) reference these
-- tables but never define them. This is the minimum the starter code touches; Phase 1 work packages
-- (1.2) extend it with sources, coverage_rows, people, job_postings, tech_detections, alerts, audit_log.
-- Run as the `migrator` role. Every tenant-owned table carries tenant_id NOT NULL; RLS is applied in 0002.
CREATE TABLE tenants (
   id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
   name           text NOT NULL,
   plan           text NOT NULL DEFAULT 'concierge',
   region         text NOT NULL DEFAULT 'ap-south-1',
   isolation_tier text NOT NULL DEFAULT 'pooled' CHECK (isolation_tier IN ('pooled', 'bridge', 'silo')),
   kms_key_arn    text,
   policy_json    jsonb NOT NULL DEFAULT '{}'::jsonb,
   created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE memberships (
   tenant_id uuid NOT NULL REFERENCES tenants(id),
   user_id   text NOT NULL,
   role      text NOT NULL CHECK (role IN ('admin', 'seller', 'viewer')),
   PRIMARY KEY (tenant_id, user_id)
);

CREATE TABLE accounts (
   id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
   tenant_id         uuid NOT NULL REFERENCES tenants(id),
   domain            text NOT NULL,
   watch_config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
   refresh_cadence   text,
   careers_url       text,
   careers_verified_at timestamptz,
   UNIQUE (tenant_id, domain)
);

CREATE TABLE facts (
   id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
   tenant_id     uuid NOT NULL REFERENCES tenants(id),
   account_id    uuid NOT NULL REFERENCES accounts(id),
   attribute_key text NOT NULL,
   value_json    jsonb NOT NULL,
   fingerprint   text NOT NULL,
   engine_grade  text CHECK (engine_grade IN ('A', 'B', 'C', 'D', 'CONFLICTING', 'BLOCKED')),
   product_grade text CHECK (product_grade IN ('confirmed', 'probable', 'hypothesis', 'lead', 'internal')),
   confidence    numeric(5,4),
   read_in_primary_this_run boolean NOT NULL DEFAULT false,   -- decision D-01
   status        text NOT NULL DEFAULT 'current',
   visibility    text NOT NULL DEFAULT 'public' CHECK (visibility IN ('public', 'private')),
   created_at    timestamptz NOT NULL DEFAULT now(),
   superseded_at timestamptz                                   -- append-only; never UPDATE the value
);

CREATE TABLE tool_calls (
   id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
   tenant_id  uuid NOT NULL REFERENCES tenants(id),
   run_id     uuid NOT NULL,
   tool       text NOT NULL,
   args_hash  text NOT NULL,
   response   jsonb,
   cost_usd   numeric(12,6) NOT NULL DEFAULT 0,
   created_at timestamptz NOT NULL DEFAULT now(),
   UNIQUE (run_id, args_hash)
);

CREATE TABLE wallets (
   tenant_id uuid PRIMARY KEY REFERENCES tenants(id),
   balance   numeric(14,4) NOT NULL DEFAULT 0 CHECK (balance >= 0)
);

CREATE TABLE wallet_ledger (
   id            bigserial PRIMARY KEY,
   tenant_id     uuid NOT NULL REFERENCES tenants(id),
   run_id        uuid,
   entry         text NOT NULL CHECK (entry IN ('reserve', 'commit', 'refund', 'topup', 'adjustment')),
   amount        numeric(14,4) NOT NULL,
   balance_after numeric(14,4) NOT NULL,
   created_at    timestamptz NOT NULL DEFAULT now()
);
