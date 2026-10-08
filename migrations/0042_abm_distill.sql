-- Migration 0042: ABM distill outputs (expand only). Spec docs/abm/, D-95. down_revision 0041.
--
-- abm_distill_outputs: insert-only hypotheses, first-call questions and narrative per run and
--   audience (client or internal), every claim tied to fact ids; the export builders read them.
-- Rollback: 0042_abm_distill.down.sql.

CREATE TABLE abm_distill_outputs (
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   run_id       uuid NOT NULL,
   audience     text NOT NULL,
   output       jsonb NOT NULL,
   created_at   timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_abm_distill_outputs PRIMARY KEY (tenant_id, run_id, audience),
   CONSTRAINT fk_abm_distill_outputs_run FOREIGN KEY (tenant_id, run_id) REFERENCES account_runs (tenant_id, id),
   CONSTRAINT ck_abm_distill_outputs_audience CHECK (audience IN ('client', 'internal'))
);
ALTER TABLE abm_distill_outputs ENABLE ROW LEVEL SECURITY;
ALTER TABLE abm_distill_outputs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON abm_distill_outputs TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON abm_distill_outputs TO app_rw;
