-- Cost drift or unknown cost: first check (read only, operator role, identifiers and counts only).
SELECT provider_code, sum(cost_micros) AS micros, sum(unknown_cost_calls) AS unknown FROM ops_cost_rollups GROUP BY provider_code ORDER BY provider_code;
