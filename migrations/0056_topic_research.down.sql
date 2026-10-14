DELETE FROM credit_entries WHERE job_id IN (SELECT id FROM research_jobs WHERE scope = 'topic');
DELETE FROM research_jobs WHERE scope = 'topic';
ALTER TABLE research_jobs DROP CONSTRAINT ck_research_jobs_topic;
ALTER TABLE research_jobs DROP CONSTRAINT ck_research_jobs_scope;
ALTER TABLE research_jobs ADD CONSTRAINT ck_research_jobs_scope
   CHECK (scope IN ('jobs', 'people', 'full', 'file'));
ALTER TABLE research_jobs DROP COLUMN topic;
