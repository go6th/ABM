-- Migration 0095: a posting's class may be decided by the heuristic (expand only).
-- The reference research classes every posting no reading decided with one rule over the platform
-- sentences (Working tool when they hold a cue word, else User role; src/jobs/heuristic.py). The
-- pipeline stores that as classified_by = 'heuristic' so an export can tell it from a reading.
-- The check constraint gains the value; no row changes. Rollback: 0095_posting_heuristic.down.sql
-- (heuristic rows are re-labelled rule first, so no class is lost).
--
-- Numbering: written as 0088 on the overnight branch, renumbered after the staging head (0093).
-- IDEMPOTENT: the constraint is dropped if present and added again with the wider list, so it
-- applies on a database that never ran it and on one that already has the wider constraint.

ALTER TABLE job_posting_classifications DROP CONSTRAINT IF EXISTS ck_job_posting_classifications_by;
ALTER TABLE job_posting_classifications ADD CONSTRAINT ck_job_posting_classifications_by
   CHECK (classified_by IN ('rule', 'extractor', 'heuristic', 'none'));
