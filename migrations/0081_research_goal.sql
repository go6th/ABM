-- Optional research goal on New research (owner decision 2026-10-23): the user may say what the
-- research is for ("we sell payroll software; find their HR stack"). It adds a few search
-- phrasings on top of each listed row's own queries and steers the distill summary; it never
-- replaces the plan. The ``topic`` column cannot carry it (migration 0056 ties topic to
-- scope = 'topic'). Additive: one nullable column, 3 to 500 characters when set.

ALTER TABLE research_jobs ADD COLUMN goal text;
ALTER TABLE research_jobs ADD CONSTRAINT ck_research_jobs_goal CHECK (
   goal IS NULL OR char_length(btrim(goal)) BETWEEN 3 AND 500);
