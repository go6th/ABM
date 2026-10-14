-- A confirmed research job carries the client's request id, so repeating the same Confirm (a
-- double click, a retried request) returns the first job and never reserves credits twice.
ALTER TABLE research_jobs ADD COLUMN request_id uuid;
CREATE UNIQUE INDEX uq_research_jobs_request ON research_jobs (tenant_id, request_id)
   WHERE request_id IS NOT NULL;
