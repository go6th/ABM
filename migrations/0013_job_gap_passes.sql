-- Migration 0013: job pipeline gap passes (expand only; no column dropped or altered).
-- Spec build_job_posting_pipeline.md section 7: a gap pass re-processes only the remainder of the
-- first pass and is stored as its own numbered record; a reclassification adds a row (pass_no on
-- classifications and sentences says which pass wrote it). job_pipeline_runs keeps the first
-- pass. New table: tenant_id NOT NULL, forced RLS (USING and WITH CHECK, TO app_rw), INSERT and
-- SELECT only. The composite unique key lets the pass row reference its run inside one tenant.
-- Rollback: 0013_job_gap_passes.down.sql.

ALTER TABLE job_pipeline_runs
   ADD CONSTRAINT uq_job_pipeline_runs_tenant_id_run_id UNIQUE (tenant_id, run_id);

ALTER TABLE job_posting_classifications
   ADD COLUMN pass_no integer NOT NULL DEFAULT 0,
   ADD CONSTRAINT ck_job_posting_classifications_pass_no CHECK (pass_no >= 0);
ALTER TABLE job_posting_platform_sentences
   ADD COLUMN pass_no integer NOT NULL DEFAULT 0,
   ADD CONSTRAINT ck_job_posting_platform_sentences_pass_no CHECK (pass_no >= 0);

CREATE TABLE job_pipeline_passes (
   tenant_id   uuid NOT NULL DEFAULT app_tenant(),
   run_id      uuid NOT NULL,
   pass_no     integer NOT NULL,
   gap_exit    text NOT NULL,
   record_json jsonb NOT NULL,     -- the PipelineResult of this pass (no posting text, no provider names)
   created_at  timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_job_pipeline_passes PRIMARY KEY (tenant_id, run_id, pass_no),
   CONSTRAINT fk_job_pipeline_passes_tenant_id_run_id_job_pipeline_runs
      FOREIGN KEY (tenant_id, run_id) REFERENCES job_pipeline_runs (tenant_id, run_id),
   CONSTRAINT ck_job_pipeline_passes_pass_no CHECK (pass_no >= 1),
   CONSTRAINT ck_job_pipeline_passes_gap_exit CHECK (gap_exit IN
      ('pending', 'converged', 'no_progress', 'budget', 'cap_reached', 'deadline'))
);
ALTER TABLE job_pipeline_passes ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_pipeline_passes FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON job_pipeline_passes TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON job_pipeline_passes TO app_rw;
