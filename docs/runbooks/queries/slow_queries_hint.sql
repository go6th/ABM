-- API latency above 500 ms at p99: first check (read only, operator role, identifiers and counts only).
SELECT state, count(*) AS sessions FROM pg_stat_activity GROUP BY state ORDER BY state;
