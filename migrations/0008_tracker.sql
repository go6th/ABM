-- migrations/0008_tracker.sql
-- Expand-only tracking tables

CREATE TABLE run_coverage_meta (
    run_id uuid PRIMARY KEY REFERENCES runs(id),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    checklist_version text NOT NULL,
    checklist_hash text NOT NULL,
    seeded_row_count int NOT NULL,
    pinned_policy jsonb NOT NULL
);

ALTER TABLE run_coverage_meta ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_coverage_meta FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_run_coverage_meta ON run_coverage_meta TO app_rw
    USING (tenant_id = (SELECT app_tenant()))
    WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON run_coverage_meta TO app_rw;


CREATE TABLE coverage_rows (
    run_id uuid REFERENCES runs(id),
    row_key text NOT NULL,
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    priority text NOT NULL CHECK (priority IN ('high', 'medium', 'optional')),
    status text CHECK (status IN ('done', 'partial', 'not_done', 'blocked', 'none')),
    reason_code text,
    proof jsonb,
    proof_hash text,
    version int NOT NULL DEFAULT 0 CHECK (version >= 0),
    attempts int NOT NULL DEFAULT 0,
    owner_stage text,
    swept boolean NOT NULL DEFAULT false,
    last_checked_at timestamptz,
    PRIMARY KEY (run_id, row_key),
    CHECK (status IS NULL OR proof IS NOT NULL),
    CHECK (status IS NULL OR status NOT IN ('not_done', 'blocked') OR reason_code IS NOT NULL),
    CHECK (NOT swept OR status = 'not_done')
);

ALTER TABLE coverage_rows ENABLE ROW LEVEL SECURITY;
ALTER TABLE coverage_rows FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_coverage_rows ON coverage_rows TO app_rw
    USING (tenant_id = (SELECT app_tenant()))
    WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE ON coverage_rows TO app_rw;

CREATE TABLE coverage_row_events (
    id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    run_id uuid NOT NULL,
    row_key text NOT NULL,
    seq int NOT NULL,
    from_status text,
    to_status text,
    actor text NOT NULL,
    stage text,
    proof_hash text,
    reason_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (run_id, row_key) REFERENCES coverage_rows(run_id, row_key),
    UNIQUE (run_id, row_key, seq)
);

ALTER TABLE coverage_row_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE coverage_row_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_coverage_row_events ON coverage_row_events TO app_rw
    USING (tenant_id = (SELECT app_tenant()))
    WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON coverage_row_events TO app_rw;

CREATE OR REPLACE FUNCTION set_event_seq() RETURNS trigger AS $$
BEGIN
    NEW.seq := COALESCE((SELECT MAX(seq) FROM coverage_row_events WHERE run_id = NEW.run_id AND row_key = NEW.row_key), 0) + 1;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER coverage_row_events_seq_trg
BEFORE INSERT ON coverage_row_events
FOR EACH ROW EXECUTE FUNCTION set_event_seq();

CREATE OR REPLACE FUNCTION check_run_completion() RETURNS trigger AS $$
DECLARE
    meta_count int;
    unset_or_swept int;
BEGIN
    IF NEW.status = 'COMPLETED' AND OLD.status != 'COMPLETED' THEN
        SELECT seeded_row_count INTO meta_count FROM run_coverage_meta WHERE run_id = NEW.id;
        IF meta_count IS NULL THEN
            RAISE EXCEPTION 'check_violation: run % has no seeded tracker', NEW.id;
        END IF;
        
        SELECT count(*) INTO unset_or_swept FROM coverage_rows 
        WHERE run_id = NEW.id AND (status IS NULL OR swept = true);
        
        IF unset_or_swept > 0 THEN
            RAISE EXCEPTION 'check_violation: run % has unset or swept rows', NEW.id;
        END IF;
        
        DECLARE
            actual_count int;
        BEGIN
            SELECT count(*) INTO actual_count FROM coverage_rows WHERE run_id = NEW.id;
            IF actual_count != meta_count THEN
                RAISE EXCEPTION 'check_violation: run % has mismatched row count', NEW.id;
            END IF;
        END;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER check_run_completion_trg
BEFORE UPDATE OF status ON runs
FOR EACH ROW EXECUTE FUNCTION check_run_completion();
