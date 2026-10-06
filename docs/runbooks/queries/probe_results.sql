-- Isolation violation or canary leak (PAGE): first check (read only, operator role, identifiers and counts only).
SELECT probe, sum(leaks) AS leaks, max(at) AS last FROM canary_probe_results GROUP BY probe ORDER BY probe;
