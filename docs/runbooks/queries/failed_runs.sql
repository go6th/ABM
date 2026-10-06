-- Checkpoint resume failures: first check (read only, operator role, identifiers and counts only).
SELECT finished_reason, count(*) AS runs FROM account_runs WHERE status = 'FAILED' GROUP BY finished_reason ORDER BY finished_reason;
