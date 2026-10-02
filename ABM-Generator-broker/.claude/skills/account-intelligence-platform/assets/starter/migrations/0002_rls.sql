-- migrations/0002_rls.sql    (run by the `migrator` role only; app_rw never owns objects)
-- Source: Architecture v2 section 18.3, with these changes (see references/13-starter-code-and-known-defects.md):
--   * defect 2: every tenant policy on a table that public_reader can read is scoped TO app_rw
--   * RLS added for the 0001 tables the listing never covered; wallet_ledger and tool_calls are insert-only
--   * ORDER: run 0001, then the LangGraph checkpointer setup() as the migrator, then this file
--     (the checkpoint block below fails if those tables do not exist)
CREATE ROLE app_rw         LOGIN NOINHERIT NOBYPASSRLS; -- API + workers
CREATE ROLE public_reader NOLOGIN;                       -- used by render_public only
GRANT public_reader TO app_rw;                           -- switched with SET LOCAL ROLE in txn

-- Fail-closed accessor: unset or empty setting -> NULL -> every policy matches zero rows.
CREATE OR REPLACE FUNCTION app_tenant() RETURNS uuid
LANGUAGE sql STABLE PARALLEL SAFE AS
$$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid $$;

CREATE TABLE account_runs (
   id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
   tenant_id         uuid NOT NULL REFERENCES tenants(id),
   account_id        uuid NOT NULL REFERENCES accounts(id),
   status            text NOT NULL DEFAULT 'QUEUED',
   graph_version     text NOT NULL,
   budget_json       jsonb NOT NULL,
   reserved_credits numeric(12,4) NOT NULL,
   idempotency_key text NOT NULL,
   created_at        timestamptz NOT NULL DEFAULT now(),
   UNIQUE (tenant_id, idempotency_key)
);
ALTER TABLE account_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_runs FORCE ROW LEVEL SECURITY;         -- applies even to the table owner
CREATE POLICY tenant_isolation ON account_runs TO app_rw
   USING       (tenant_id = (SELECT app_tenant()))          -- (SELECT ..) = evaluated once per query
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE ON account_runs TO app_rw;


-- RLS for the remaining 0001 tables (not in the ARCH listing). Same pattern, scoped TO app_rw.
-- lint:covers memberships, accounts, facts, tool_calls, wallets, wallet_ledger
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['memberships','accounts','facts','tool_calls','wallets','wallet_ledger'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format($p$CREATE POLICY tenant_isolation ON %I TO app_rw
      USING      (tenant_id = (SELECT app_tenant()))
      WITH CHECK (tenant_id = (SELECT app_tenant()))$p$, t);
  END LOOP;
END $$;
-- tenants is keyed by id, not tenant_id: a tenant can see only itself.
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_self ON tenants TO app_rw
  USING (id = (SELECT app_tenant())) WITH CHECK (id = (SELECT app_tenant()));
GRANT SELECT ON tenants TO app_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON memberships TO app_rw;
GRANT SELECT, INSERT, UPDATE ON accounts, facts, wallets TO app_rw;
GRANT SELECT, INSERT ON tool_calls, wallet_ledger TO app_rw;      -- insert-only: no UPDATE, no DELETE
GRANT USAGE ON SEQUENCE wallet_ledger_id_seq TO app_rw;

-- Two-output rule at the database layer: public_reader cannot see private rows at all.
CREATE POLICY public_only ON facts FOR SELECT TO public_reader
  USING (tenant_id = (SELECT app_tenant()) AND visibility = 'public');
GRANT SELECT ON facts TO public_reader;                   -- no grant at all on chunks

-- Library-owned LangGraph tables: thread_id = '<tenant_uuid>:<run_uuid>'
-- lint:covers checkpoints, checkpoint_blobs, checkpoint_writes
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['checkpoints','checkpoint_blobs','checkpoint_writes'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format($p$CREATE POLICY tenant_prefix ON %I
      USING      (starts_with(thread_id, (SELECT app_tenant())::text || ':'))
      WITH CHECK (starts_with(thread_id, (SELECT app_tenant())::text || ':'))$p$, t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO app_rw', t);
  END LOOP;
END $$;

-- Vector table: hash-partitioned by tenant, halfvec to halve memory, HNSW per partition.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE chunks (
   id          uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id uuid NOT NULL,
   account_id uuid NOT NULL,
   doc_id      uuid NOT NULL,
   visibility text NOT NULL DEFAULT 'private' CHECK (visibility = 'private'),
   content     text NOT NULL,
   tsv         tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
   embedding halfvec(1536) NOT NULL,
   PRIMARY KEY (tenant_id, id)
) PARTITION BY HASH (tenant_id);
-- 16 hash partitions, each with its own HNSW index (the listing describes this but does not show it)
DO $$
BEGIN
  FOR i IN 0..15 LOOP
    EXECUTE format('CREATE TABLE chunks_p%s PARTITION OF chunks FOR VALUES WITH (MODULUS 16, REMAINDER %s)', i, i);
    EXECUTE format('CREATE INDEX ON chunks_p%s USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)', i);
  END LOOP;
END $$;
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON chunks TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, DELETE ON chunks TO app_rw;
