-- Runs missing the duration target: first check (read only, operator role, identifiers and counts only).
SELECT status, count(*) AS runs, percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM finished_at - created_at)) AS p95_s FROM account_runs WHERE finished_at IS NOT NULL GROUP BY status ORDER BY status;
