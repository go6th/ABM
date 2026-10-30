-- Undo migration 0095: classified_by loses 'heuristic'. Heuristic rows are re-labelled 'rule'
-- first (the class and reason stay, so no class is lost) so the narrower check holds. 'rule' is
-- the closest old value and states how the class was decided: the heuristic is one fixed rule over
-- the platform sentences (src/jobs/heuristic.py), never a reading; the reason text still says
-- heuristic_*, which is how a row decided this way stays recognisable after a rollback.
UPDATE job_posting_classifications SET classified_by = 'rule' WHERE classified_by = 'heuristic';
ALTER TABLE job_posting_classifications DROP CONSTRAINT IF EXISTS ck_job_posting_classifications_by;
ALTER TABLE job_posting_classifications ADD CONSTRAINT ck_job_posting_classifications_by
   CHECK (classified_by IN ('rule', 'extractor', 'none'));
