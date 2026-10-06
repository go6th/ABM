-- API availability burn: first check (read only, operator role, identifiers and counts only).
SELECT status, count(*) AS runs FROM account_runs WHERE created_at > now() - interval '1 hour' GROUP BY status ORDER BY status;
