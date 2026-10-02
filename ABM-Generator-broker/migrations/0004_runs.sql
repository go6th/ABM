-- Data layer v1, step 8: runs.
-- One full run or refresh of an account. Pins the config version, carries the agreed credit
-- cap, and links a refresh to the run it refreshes. Rows are never deleted.

-- Composite foreign keys keep child rows inside their parent's tenant. A plain FK check
-- bypasses RLS, so without these a row could point at another tenant's account by id.
ALTER TABLE accounts ADD CONSTRAINT uq_accounts_tenant_id_id UNIQUE (tenant_id, id);

CREATE TABLE runs (
   id             uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id      uuid NOT NULL DEFAULT app_tenant(),
   account_id     uuid NOT NULL,
   run_type       text NOT NULL,
   status         text NOT NULL DEFAULT 'queued',
   config_version text NOT NULL,
   credit_cap     integer,
   parent_run_id  uuid,
   started_at     timestamptz,
   finished_at    timestamptz,
   error          text,
   created_at     timestamptz NOT NULL DEFAULT now(),
   updated_at     timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_runs PRIMARY KEY (id),
   CONSTRAINT uq_runs_tenant_id_account_id_id UNIQUE (tenant_id, account_id, id),
   CONSTRAINT fk_runs_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT fk_runs_tenant_id_account_id_accounts
      FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
   -- A refresh's parent is a run of the same account in the same tenant.
   CONSTRAINT fk_runs_tenant_id_account_id_parent_run_id_runs
      FOREIGN KEY (tenant_id, account_id, parent_run_id) REFERENCES runs (tenant_id, account_id, id),
   CONSTRAINT ck_runs_run_type CHECK (run_type IN ('full', 'refresh')),
   CONSTRAINT ck_runs_status CHECK (status IN ('queued', 'running', 'paused', 'failed', 'completed')),
   CONSTRAINT ck_runs_config_version_not_blank CHECK (btrim(config_version) <> ''),
   CONSTRAINT ck_runs_credit_cap_positive CHECK (credit_cap > 0),
   -- The cap must be agreed before any work starts.
   CONSTRAINT ck_runs_credit_cap_before_start CHECK (status = 'queued' OR credit_cap IS NOT NULL),
   CONSTRAINT ck_runs_refresh_has_parent CHECK ((run_type = 'refresh') = (parent_run_id IS NOT NULL)),
   CONSTRAINT ck_runs_parent_is_not_self CHECK (parent_run_id <> id),
   CONSTRAINT ck_runs_finished_after_started CHECK (finished_at >= started_at),
   -- Timestamps follow the lifecycle: queued has neither, active has a start only,
   -- completed has both, failed has an end and a reason (it may fail before starting).
   CONSTRAINT ck_runs_lifecycle_timestamps CHECK (
      CASE status
         WHEN 'queued'    THEN started_at IS NULL AND finished_at IS NULL
         WHEN 'running'   THEN started_at IS NOT NULL AND finished_at IS NULL
         WHEN 'paused'    THEN started_at IS NOT NULL AND finished_at IS NULL
         WHEN 'completed' THEN started_at IS NOT NULL AND finished_at IS NOT NULL
         WHEN 'failed'    THEN finished_at IS NOT NULL
      END
   ),
   CONSTRAINT ck_runs_error_only_when_failed CHECK ((status = 'failed') = (error IS NOT NULL))
);
CREATE INDEX ix_runs_account_id_created_at ON runs (account_id, created_at DESC);
CREATE INDEX ix_runs_status ON runs (status);
CREATE INDEX ix_runs_parent_run_id ON runs (parent_run_id) WHERE parent_run_id IS NOT NULL;

-- Status transitions; mirrors RUN_TRANSITIONS in src/account_intelligence/db/enums.py.
-- Identity columns never change after insert.
CREATE FUNCTION runs_guard_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
   IF NEW.tenant_id <> OLD.tenant_id OR NEW.account_id <> OLD.account_id
      OR NEW.run_type <> OLD.run_type
      OR NEW.parent_run_id IS DISTINCT FROM OLD.parent_run_id THEN
      RAISE EXCEPTION 'runs: tenant_id, account_id, run_type and parent_run_id are immutable'
         USING ERRCODE = 'check_violation';
   END IF;
   IF NEW.status <> OLD.status AND NOT (
         (OLD.status = 'queued'  AND NEW.status IN ('running', 'failed'))
      OR (OLD.status = 'running' AND NEW.status IN ('paused', 'failed', 'completed'))
      OR (OLD.status = 'paused'  AND NEW.status IN ('running', 'failed'))
   ) THEN
      RAISE EXCEPTION 'runs: status cannot change from % to %', OLD.status, NEW.status
         USING ERRCODE = 'check_violation';
   END IF;
   RETURN NEW;
END $$;
CREATE TRIGGER runs_guard_update BEFORE UPDATE ON runs
   FOR EACH ROW EXECUTE FUNCTION runs_guard_update();

ALTER TABLE runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE runs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON runs TO app_rw
   USING      (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE ON runs TO app_rw;          -- no DELETE: runs are history
