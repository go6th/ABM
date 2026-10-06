-- Stuck QUEUED or RUNNING runs: first check (read only, operator role, identifiers and counts only).
SELECT state, reason, age_bucket, count(*) AS runs FROM stuck_run_observations WHERE at > now() - interval '1 hour' GROUP BY state, reason, age_bucket ORDER BY state, reason;
