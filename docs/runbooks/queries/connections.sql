-- Database failover: first check (read only, operator role, identifiers and counts only).
SELECT datname, count(*) AS connections FROM pg_stat_activity GROUP BY datname ORDER BY datname;
