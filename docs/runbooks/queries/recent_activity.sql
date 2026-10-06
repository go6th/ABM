-- Telemetry down (silence is an alert): first check (read only, operator role, identifiers and counts only).
SELECT max(created_at) AS last_run_created FROM account_runs;
