-- Rollback of 0010: drop what 0010 added and restore the 0008 function body.
DROP TABLE IF EXISTS tech_detections;
DROP TABLE IF EXISTS people;
DROP TABLE IF EXISTS job_postings;
DROP INDEX IF EXISTS uq_wallet_ledger_settlement_per_run;
DROP INDEX IF EXISTS uq_wallet_ledger_reserve_per_run;
DROP TRIGGER IF EXISTS account_runs_guard_status ON account_runs;
DROP FUNCTION IF EXISTS account_runs_guard_status();
ALTER TABLE account_runs
   DROP CONSTRAINT IF EXISTS ck_account_runs_status, DROP CONSTRAINT IF EXISTS ck_account_runs_finished,
   DROP CONSTRAINT IF EXISTS ck_account_runs_slices,
   DROP COLUMN IF EXISTS finished_at, DROP COLUMN IF EXISTS finished_reason,
   DROP COLUMN IF EXISTS approval_deadline, DROP COLUMN IF EXISTS request_hash, DROP COLUMN IF EXISTS slices_used;
ALTER TRIGGER runs_zz_completion_guard ON runs RENAME TO check_run_completion_trg;
-- the original 0008 body, verbatim
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
DROP FUNCTION IF EXISTS assert_run_completable(uuid);
