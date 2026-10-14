-- Reverse 0054_research_job_requests: drop the request id on research jobs.
DROP INDEX IF EXISTS uq_research_jobs_request;
ALTER TABLE research_jobs DROP COLUMN request_id;
