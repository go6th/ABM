-- Migration 0018: model routing, verifier findings and the eval harness (expand only; no existing
-- column is dropped or altered). Spec docs/prompts/build_routing_verify_eval.md section 5.
-- down_revision 0017.
--
-- No model-call ledger existed before this revision, so it is created here: llm_calls (one row per
-- logical call, replayed by call key, R9) and llm_call_attempts (failed attempts: the router's
-- selection history). Both insert-only, forced RLS TO app_rw.
-- llm_candidate_health: shared circuit state per candidate, mutable and documented as NOT a
-- ledger; no tenant column (a candidate's health is the same for every tenant); written only by
-- llm_health_record (security invoker, one conditional statement per event).
-- verifier_findings: insert-only, one row per (run, claim, source text, prompt, verifier) (V8).
-- eval_runs, eval_case_results, eval_metrics, eval_baselines: insert-only, owned by a dedicated
-- synthetic evaluation tenant so forced RLS still applies (E8: a better baseline is a new row).
-- Tenant views: public_reader has no grant on any table here.
-- Rollback: 0018_routing_verify_eval.down.sql.

CREATE TABLE llm_calls (
   id                 bigserial NOT NULL,
   tenant_id          uuid NOT NULL DEFAULT app_tenant(),
   run_id             uuid NOT NULL,
   call_key           text NOT NULL,
   task_kind          text NOT NULL,
   tier               text NOT NULL,
   candidate          text NOT NULL,
   model_alias        text NOT NULL,
   provider_code      text NOT NULL,
   family             text NOT NULL,
   routing_version    text NOT NULL,
   prompt_hash        text NOT NULL,
   degraded           boolean NOT NULL,
   degraded_reason    text,
   input_tokens       integer NOT NULL,
   output_tokens      integer NOT NULL,
   cache_read_tokens  integer NOT NULL DEFAULT 0,
   cache_write_tokens integer NOT NULL DEFAULT 0,
   cost_usd           numeric(14,6),
   cost_known         boolean NOT NULL,
   output_json        jsonb NOT NULL,
   created_at         timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_llm_calls PRIMARY KEY (id),
   CONSTRAINT fk_llm_calls_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT uq_llm_calls_run_key UNIQUE (run_id, call_key),
   CONSTRAINT ck_llm_calls_tokens CHECK (input_tokens >= 0 AND output_tokens >= 0
      AND cache_read_tokens >= 0 AND cache_write_tokens >= 0),
   -- an unknown price is NULL with cost_known false, never zero (R9)
   CONSTRAINT ck_llm_calls_cost CHECK ((cost_usd IS NULL) = (NOT cost_known)),
   CONSTRAINT ck_llm_calls_degraded CHECK (degraded OR degraded_reason IS NULL),
   CONSTRAINT ck_llm_calls_output_size CHECK (octet_length(output_json::text) <= 262144)
);
ALTER TABLE llm_calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm_calls FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON llm_calls TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON llm_calls TO app_rw;
GRANT USAGE ON SEQUENCE llm_calls_id_seq TO app_rw;

CREATE TABLE llm_call_attempts (
   id          bigserial NOT NULL,
   tenant_id   uuid NOT NULL DEFAULT app_tenant(),
   run_id      uuid NOT NULL,
   call_key    text NOT NULL,
   candidate   text NOT NULL,
   error_class text NOT NULL,
   at          timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_llm_call_attempts PRIMARY KEY (id),
   CONSTRAINT fk_llm_call_attempts_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_llm_call_attempts_class CHECK (error_class IN ('Overloaded', 'ModelUnavailable',
      'ContextTooLong', 'SchemaInvalid', 'AuthFailed', 'ContentRefused'))
);
ALTER TABLE llm_call_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm_call_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON llm_call_attempts TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON llm_call_attempts TO app_rw;
GRANT USAGE ON SEQUENCE llm_call_attempts_id_seq TO app_rw;
CREATE INDEX ix_llm_call_attempts_key ON llm_call_attempts (run_id, call_key, id);

CREATE TABLE llm_candidate_health (
   candidate    text NOT NULL,
   failures     integer NOT NULL DEFAULT 0,
   window_start timestamptz,
   open_until   timestamptz,
   updated_at   timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_llm_candidate_health PRIMARY KEY (candidate),
   CONSTRAINT ck_llm_candidate_health_failures CHECK (failures >= 0)
);
GRANT SELECT, INSERT, UPDATE ON llm_candidate_health TO app_rw;

-- llm.health.step in SQL (one statement per event under the row lock; security invoker).
CREATE FUNCTION llm_health_record(p_candidate text, p_event text, p_now timestamptz,
   p_threshold integer, p_window_s double precision, p_cooldown_s double precision)
   RETURNS void LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE
   v llm_candidate_health%ROWTYPE;
   v_failures integer;
   v_start timestamptz;
BEGIN
   IF p_event NOT IN ('failure', 'success') THEN
      RAISE EXCEPTION 'llm health: unknown event';
   END IF;
   INSERT INTO llm_candidate_health (candidate) VALUES (p_candidate)
   ON CONFLICT (candidate) DO NOTHING;
   SELECT * INTO v FROM llm_candidate_health WHERE candidate = p_candidate FOR UPDATE;
   IF p_event = 'success' THEN
      UPDATE llm_candidate_health SET failures = 0, window_start = NULL, open_until = NULL,
         updated_at = p_now WHERE candidate = p_candidate;
      RETURN;
   END IF;
   v_failures := v.failures;
   v_start := v.window_start;
   IF v_start IS NULL OR p_now - v_start > make_interval(secs => p_window_s) THEN
      v_failures := 0;
      v_start := p_now;
   END IF;
   v_failures := v_failures + 1;
   IF v_failures >= p_threshold THEN
      UPDATE llm_candidate_health SET failures = 0, window_start = NULL,
         open_until = p_now + make_interval(secs => p_cooldown_s), updated_at = p_now
       WHERE candidate = p_candidate;
   ELSE
      UPDATE llm_candidate_health SET failures = v_failures, window_start = v_start,
         updated_at = p_now WHERE candidate = p_candidate;
   END IF;
END
$$;
REVOKE ALL ON FUNCTION llm_health_record(text, text, timestamptz, integer, double precision,
   double precision) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION llm_health_record(text, text, timestamptz, integer, double precision,
   double precision) TO app_rw;

CREATE TABLE verifier_findings (
   id               bigserial NOT NULL,
   tenant_id        uuid NOT NULL DEFAULT app_tenant(),
   run_id           uuid NOT NULL,
   kind             text NOT NULL,
   subject_key      text NOT NULL,
   fact_id          uuid,
   source_text_hash text NOT NULL,
   outcome          text NOT NULL,
   action           text NOT NULL,
   verifier_alias   text NOT NULL,
   prompt_hash      text NOT NULL,
   span_hashes      text[] NOT NULL DEFAULT '{}',
   detail_json      jsonb NOT NULL DEFAULT '{}'::jsonb,
   at               timestamptz NOT NULL,
   CONSTRAINT pk_verifier_findings PRIMARY KEY (id),
   CONSTRAINT fk_verifier_findings_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT uq_verifier_findings_key UNIQUE (run_id, kind, subject_key, source_text_hash,
      prompt_hash, verifier_alias),
   CONSTRAINT ck_verifier_findings_kind CHECK (kind IN ('claim', 'figure', 'narrative')),
   CONSTRAINT ck_verifier_findings_outcome CHECK (outcome IN ('confirmed', 'value_mismatch',
      'not_found', 'unreadable', 'unavailable', 'not_independent', 'figure_ok',
      'figure_mismatch', 'figure_unregistered', 'supported', 'overstated', 'unsupported',
      'contradicted')),
   CONSTRAINT ck_verifier_findings_action CHECK (action IN ('keep', 'downgrade', 'drop', 'flag',
      'rephrase', 'remove'))
);
ALTER TABLE verifier_findings ENABLE ROW LEVEL SECURITY;
ALTER TABLE verifier_findings FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON verifier_findings TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON verifier_findings TO app_rw;
GRANT USAGE ON SEQUENCE verifier_findings_id_seq TO app_rw;

CREATE TABLE eval_runs (
   id              uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id       uuid NOT NULL DEFAULT app_tenant(),
   mode            text NOT NULL,
   status          text NOT NULL,
   code_sha        text NOT NULL,
   prompts_hash    text NOT NULL,
   routing_hash    text NOT NULL,
   golden_version  text NOT NULL,
   seed            bigint NOT NULL,
   report_json     jsonb NOT NULL,
   created_at      timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_eval_runs PRIMARY KEY (id),
   CONSTRAINT fk_eval_runs_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT uq_eval_runs_tenant_id UNIQUE (tenant_id, id),
   CONSTRAINT ck_eval_runs_mode CHECK (mode IN ('replay', 'live', 'nightly')),
   CONSTRAINT ck_eval_runs_status CHECK (status IN ('pass', 'block', 'not_run',
      'budget_exceeded', 'infrastructure_error', 'timeout'))
);
ALTER TABLE eval_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE eval_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON eval_runs TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON eval_runs TO app_rw;

CREATE TABLE eval_case_results (
   id           bigserial NOT NULL,
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   eval_run_id  uuid NOT NULL,
   account      text NOT NULL,
   repeat_no    integer NOT NULL,
   result_json  jsonb NOT NULL,
   CONSTRAINT pk_eval_case_results PRIMARY KEY (id),
   CONSTRAINT fk_eval_case_results_run FOREIGN KEY (tenant_id, eval_run_id)
      REFERENCES eval_runs (tenant_id, id),
   CONSTRAINT uq_eval_case_results UNIQUE (eval_run_id, account, repeat_no)
);
ALTER TABLE eval_case_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE eval_case_results FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON eval_case_results TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON eval_case_results TO app_rw;
GRANT USAGE ON SEQUENCE eval_case_results_id_seq TO app_rw;

CREATE TABLE eval_metrics (
   id           bigserial NOT NULL,
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   eval_run_id  uuid NOT NULL,
   metric       text NOT NULL,
   value        double precision,
   numerator    bigint,
   denominator  bigint,
   ci_low       double precision,
   ci_high      double precision,
   status       text NOT NULL,
   CONSTRAINT pk_eval_metrics PRIMARY KEY (id),
   CONSTRAINT fk_eval_metrics_run FOREIGN KEY (tenant_id, eval_run_id)
      REFERENCES eval_runs (tenant_id, id),
   CONSTRAINT uq_eval_metrics UNIQUE (eval_run_id, metric),
   CONSTRAINT ck_eval_metrics_status CHECK (status IN ('measured', 'not_applicable', 'not_run'))
);
ALTER TABLE eval_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE eval_metrics FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON eval_metrics TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON eval_metrics TO app_rw;
GRANT USAGE ON SEQUENCE eval_metrics_id_seq TO app_rw;

CREATE TABLE eval_baselines (
   id           bigserial NOT NULL,
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   eval_run_id  uuid NOT NULL,
   branch       text NOT NULL,
   code_sha     text NOT NULL,
   created_at   timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_eval_baselines PRIMARY KEY (id),
   CONSTRAINT fk_eval_baselines_run FOREIGN KEY (tenant_id, eval_run_id)
      REFERENCES eval_runs (tenant_id, id)
);
ALTER TABLE eval_baselines ENABLE ROW LEVEL SECURITY;
ALTER TABLE eval_baselines FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON eval_baselines TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON eval_baselines TO app_rw;
GRANT USAGE ON SEQUENCE eval_baselines_id_seq TO app_rw;
