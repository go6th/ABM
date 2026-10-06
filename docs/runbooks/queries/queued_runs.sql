-- Workflow engine (task queue) outage: first check (read only, operator role, identifiers and counts only).
SELECT count(*) AS queued, min(created_at) AS oldest FROM account_runs WHERE status = 'QUEUED';
