-- Undo migration 0081: the research goal.
ALTER TABLE research_jobs DROP CONSTRAINT IF EXISTS ck_research_jobs_goal;
ALTER TABLE research_jobs DROP COLUMN IF EXISTS goal;
