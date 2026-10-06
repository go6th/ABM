-- Review queue age: first check (read only, operator role, identifiers and counts only).
SELECT awaiting_kind, count(*) AS runs, min(approval_deadline) AS first_deadline FROM account_runs WHERE status = 'AWAITING_APPROVAL' GROUP BY awaiting_kind ORDER BY awaiting_kind;
