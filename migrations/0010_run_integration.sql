-- Migration 0010: run integration (expand only; no existing column is dropped or altered).
-- Intent:
--   1. Fix the tracker completion guard (AUDIT F-3): 0008 compared runs.status with 'COMPLETED', but
--      runs.status only allows lowercase values, so the guard never fired. The function is replaced;
--      the guard now also covers account_runs (the orchestration run row, same id as runs).
--   2. account_runs: status vocabulary CHECK (skill reference 04 state machine), terminal-state guard,
--      finish fields, approval deadline, and a request hash for idempotency-key reuse (known defect 5).
--   3. wallet_ledger: one reservation and one settlement per run, enforced by partial unique indexes.
--   4. New tenant tables for pipelines that had no storage: job_postings, people, tech_detections.
-- Rollback: 0010_run_integration.down.sql (drops what this file adds, restores the 0008 function).

-- 1. Completion guard ----------------------------------------------------------------------------
-- Shared check: the run's tracker is seeded, the seeded row count matches, and no row is unset or
-- swept. Raises check_violation; used by both run tables. SECURITY INVOKER: runs under RLS as the
-- caller, which sees exactly the run's own tenant rows.
CREATE OR REPLACE FUNCTION assert_run_completable(p_run_id uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
   meta_count int;
   actual_count int;
   open_rows int;
BEGIN
   SELECT seeded_row_count INTO meta_count FROM run_coverage_meta WHERE run_id = p_run_id;
   IF meta_count IS NULL THEN
      RAISE EXCEPTION 'run % cannot complete: no seeded tracker', p_run_id USING ERRCODE = 'check_violation';
   END IF;
   SELECT count(*), count(*) FILTER (WHERE status IS NULL OR swept)
     INTO actual_count, open_rows FROM coverage_rows WHERE run_id = p_run_id;
   IF actual_count <> meta_count THEN
      RAISE EXCEPTION 'run % cannot complete: tracker row count mismatch', p_run_id USING ERRCODE = 'check_violation';
   END IF;
   IF open_rows > 0 THEN
      RAISE EXCEPTION 'run % cannot complete: % unset or swept rows', p_run_id, open_rows USING ERRCODE = 'check_violation';
   END IF;
END $$;

-- runs (data-layer lifecycle row): replaces the 0008 body, which tested the wrong case.
CREATE OR REPLACE FUNCTION check_run_completion() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
   IF NEW.status = 'completed' AND OLD.status IS DISTINCT FROM 'completed' THEN
      PERFORM assert_run_completable(NEW.id);
   END IF;
   RETURN NEW;
END $$;

-- Lifecycle validation (runs_guard_update) must fire before the completion guard: PostgreSQL fires
-- BEFORE triggers in name order, so the guard trigger is renamed to sort after it.
ALTER TRIGGER check_run_completion_trg ON runs RENAME TO runs_zz_completion_guard;

-- 2. account_runs -----------------------------------------------------------------------------------
ALTER TABLE account_runs
   ADD COLUMN finished_at      timestamptz,
   ADD COLUMN finished_reason  text,
   ADD COLUMN approval_deadline timestamptz,
   ADD COLUMN request_hash     text,
   ADD COLUMN slices_used      integer NOT NULL DEFAULT 0,
   -- Status vocabulary of skill reference 04 (terminal: COMPLETED, PARTIAL, FAILED, CANCELED, BUDGET_EXCEEDED).
   ADD CONSTRAINT ck_account_runs_status CHECK (status IN (
      'QUEUED', 'RUNNING', 'AWAITING_APPROVAL', 'DISTILLING', 'VERIFYING',
      'COMPLETED', 'PARTIAL', 'FAILED', 'CANCELED', 'BUDGET_EXCEEDED')) NOT VALID,
   -- A terminal run records why and when it ended.
   ADD CONSTRAINT ck_account_runs_finished CHECK (
      (status IN ('COMPLETED', 'PARTIAL', 'FAILED', 'CANCELED', 'BUDGET_EXCEEDED')) = (finished_at IS NOT NULL)) NOT VALID,
   ADD CONSTRAINT ck_account_runs_slices CHECK (slices_used >= 0);
ALTER TABLE account_runs VALIDATE CONSTRAINT ck_account_runs_status;
ALTER TABLE account_runs VALIDATE CONSTRAINT ck_account_runs_finished;

-- Terminal states have no exits; COMPLETED additionally needs a completable tracker.
CREATE FUNCTION account_runs_guard_status() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
   IF NEW.status IS DISTINCT FROM OLD.status THEN
      IF OLD.status IN ('COMPLETED', 'PARTIAL', 'FAILED', 'CANCELED', 'BUDGET_EXCEEDED') THEN
         RAISE EXCEPTION 'account_runs: % is terminal; cannot move to %', OLD.status, NEW.status
            USING ERRCODE = 'check_violation';
      END IF;
      IF NEW.status = 'COMPLETED' THEN
         PERFORM assert_run_completable(NEW.id);
      END IF;
   END IF;
   RETURN NEW;
END $$;
CREATE TRIGGER account_runs_guard_status BEFORE UPDATE OF status ON account_runs
   FOR EACH ROW EXECUTE FUNCTION account_runs_guard_status();

-- 3. Wallet: one reservation and one settlement (commit or refund entry, at most one each) per run.
CREATE UNIQUE INDEX uq_wallet_ledger_reserve_per_run ON wallet_ledger (run_id) WHERE entry = 'reserve';
CREATE UNIQUE INDEX uq_wallet_ledger_settlement_per_run ON wallet_ledger (run_id, entry)
   WHERE entry IN ('commit', 'refund');

-- 4. Pipeline tables -------------------------------------------------------------------------------
-- Job postings: one row per deduplicated posting per run (pull once: re-entry reads the rows back).
CREATE TABLE job_postings (
   id            uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id     uuid NOT NULL DEFAULT app_tenant(),
   account_id    uuid NOT NULL,
   run_id        uuid NOT NULL,
   posting_key   text NOT NULL,
   row_json      jsonb NOT NULL,   -- the validated jobs.models.Posting, written only by the job repo
   status        text NOT NULL,
   window_bucket text NOT NULL,
   created_at    timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_job_postings PRIMARY KEY (id),
   CONSTRAINT uq_job_postings_tenant_id_run_id_posting_key UNIQUE (tenant_id, run_id, posting_key),
   CONSTRAINT fk_job_postings_tenant_id_account_id_accounts
      FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
   CONSTRAINT ck_job_postings_status CHECK (status IN ('open', 'closed', 'unknown')),
   CONSTRAINT ck_job_postings_window CHECK (window_bucket IN ('in_window', 'out_of_window', 'undated'))
);
ALTER TABLE job_postings ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_postings FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON job_postings TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON job_postings TO app_rw;          -- insert-only: postings are observations

-- People: one row per person per run, deduplicated by profile URL (PRD: profile URL first).
CREATE TABLE people (
   id                  uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id           uuid NOT NULL DEFAULT app_tenant(),
   account_id          uuid NOT NULL,
   run_id              uuid NOT NULL,
   profile_url         text,
   provider_person_id  text,
   name                text,
   title               text,
   job_level           text,
   source_ref          text NOT NULL,
   excluded            boolean NOT NULL DEFAULT false,
   excluded_reason     text,
   created_at          timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_people PRIMARY KEY (id),
   CONSTRAINT fk_people_tenant_id_account_id_accounts
      FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
   CONSTRAINT ck_people_excluded_reason CHECK (excluded = (excluded_reason IS NOT NULL)),
   CONSTRAINT ck_people_identity CHECK (profile_url IS NOT NULL OR provider_person_id IS NOT NULL)
);
CREATE UNIQUE INDEX uq_people_run_profile ON people (tenant_id, run_id, profile_url) WHERE profile_url IS NOT NULL;
ALTER TABLE people ENABLE ROW LEVEL SECURITY;
ALTER TABLE people FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON people TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON people TO app_rw;                -- merged and excluded rows are kept (insert-only)

-- Technology detections: first and last seen per technology; a date range alone is a lead (grading).
CREATE TABLE tech_detections (
   id              uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id       uuid NOT NULL DEFAULT app_tenant(),
   account_id      uuid NOT NULL,
   run_id          uuid NOT NULL,
   technology      text NOT NULL,
   first_seen_at   text,
   last_seen_at    text,
   source_count    integer,
   source_ref      text NOT NULL,
   created_at      timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_tech_detections PRIMARY KEY (id),
   CONSTRAINT uq_tech_detections_tenant_id_run_id_technology UNIQUE (tenant_id, run_id, technology),
   CONSTRAINT fk_tech_detections_tenant_id_account_id_accounts
      FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id)
);
ALTER TABLE tech_detections ENABLE ROW LEVEL SECURITY;
ALTER TABLE tech_detections FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tech_detections TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON tech_detections TO app_rw;
