-- Migration 0033: data model conformance (cross-cutting X1; expand only). Spec
-- docs/prompts/build_crosscutting.md s3 DM1 to DM7. down_revision 0032.
--
-- accounts.careers_fetch_ref: the fetch record proving the careers URL was visited; a careers URL
--   can only be stored with its verified time and fetch record (CHECK, NOT VALID: enforced for new
--   and changed rows, existing rows are not rewritten).
-- account_runs.checklist_version, account_runs.model_versions: pinned at creation with the graph
--   version and the grading config; a trigger makes every pinned column immutable (the graph version
--   never changes; the others may be set once from empty).
-- facts: a Hypothesis carries its confirming question (CHECK, NOT VALID).
-- tech_detections.cross_check_result: a detection with only a date range is a Lead until this is
--   set (enforced by datamodel.counts.tech_grade_cap).
-- Rollback: 0033_datamodel.down.sql.

ALTER TABLE accounts ADD COLUMN careers_fetch_ref text;
ALTER TABLE accounts ADD CONSTRAINT ck_accounts_careers_url_visited CHECK (careers_url IS NULL
   OR (careers_url_verified_at IS NOT NULL AND careers_fetch_ref IS NOT NULL
       AND careers_url ~ '^https?://')) NOT VALID;

ALTER TABLE account_runs ADD COLUMN checklist_version text,
   ADD COLUMN model_versions jsonb;
ALTER TABLE account_runs ADD CONSTRAINT ck_account_runs_model_versions CHECK (model_versions IS NULL
   OR jsonb_typeof(model_versions) = 'object') NOT VALID;

CREATE FUNCTION account_runs_guard_pins() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
   -- the graph version never changes; the other pins may be set once (from empty), never changed
   IF NEW.graph_version IS DISTINCT FROM OLD.graph_version
      OR (OLD.checklist_version IS NOT NULL
          AND NEW.checklist_version IS DISTINCT FROM OLD.checklist_version)
      OR (OLD.model_versions IS NOT NULL
          AND NEW.model_versions IS DISTINCT FROM OLD.model_versions)
      OR (OLD.settings_version IS NOT NULL
          AND NEW.settings_version IS DISTINCT FROM OLD.settings_version)
      OR (OLD.grading_config_hash IS NOT NULL
          AND NEW.grading_config_hash IS DISTINCT FROM OLD.grading_config_hash) THEN
      RAISE EXCEPTION 'account_runs: pinned versions are immutable after creation'
         USING ERRCODE = 'check_violation';
   END IF;
   RETURN NEW;
END $$;
CREATE TRIGGER account_runs_guard_pins BEFORE UPDATE ON account_runs
   FOR EACH ROW EXECUTE FUNCTION account_runs_guard_pins();

ALTER TABLE facts ADD CONSTRAINT ck_facts_hypothesis_has_question CHECK (
   product_grade IS DISTINCT FROM 'hypothesis'
   OR (confirming_question IS NOT NULL AND char_length(confirming_question) > 0)) NOT VALID;

ALTER TABLE tech_detections ADD COLUMN cross_check_result text;
ALTER TABLE tech_detections ADD CONSTRAINT ck_tech_detections_cross_check CHECK (
   cross_check_result IS NULL OR cross_check_result IN ('confirmed', 'contradicted',
   'not_found')) NOT VALID;

-- export_verifications: insert-only results of the export verifier (DM7): the row hash recomputed
-- from the stored rows, the file bytes against their recorded sha256 and the rows re-extracted from
-- the file. A failed verification blocks the package's downloads.
CREATE TABLE export_verifications (
   id           bigserial NOT NULL,
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   package_id   uuid NOT NULL,
   ok           boolean NOT NULL,
   problems     jsonb NOT NULL,
   at           timestamptz NOT NULL,
   CONSTRAINT pk_export_verifications PRIMARY KEY (id),
   CONSTRAINT fk_export_verifications_package FOREIGN KEY (package_id) REFERENCES export_packages (id),
   CONSTRAINT ck_export_verifications_problems CHECK (jsonb_typeof(problems) = 'array'
      AND (ok = (jsonb_array_length(problems) = 0)))
);
ALTER TABLE export_verifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE export_verifications FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON export_verifications TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON export_verifications TO app_rw;
GRANT USAGE ON SEQUENCE export_verifications_id_seq TO app_rw;
