-- Quality gate failure spike: first check (read only, operator role, identifiers and counts only).
SELECT finished_reason, count(*) AS runs FROM account_runs WHERE status = 'PARTIAL' GROUP BY finished_reason ORDER BY runs DESC;
