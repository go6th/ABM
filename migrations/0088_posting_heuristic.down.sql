-- Undo migration 0088: classified_by loses 'heuristic'. Heuristic rows are re-labelled 'rule'
-- first (the class and reason stay, so no class is lost) so the narrower check holds.
UPDATE job_posting_classifications SET classified_by = 'rule' WHERE classified_by = 'heuristic';
ALTER TABLE job_posting_classifications DROP CONSTRAINT ck_job_posting_classifications_by;
ALTER TABLE job_posting_classifications ADD CONSTRAINT ck_job_posting_classifications_by
   CHECK (classified_by IN ('rule', 'extractor', 'none'));
