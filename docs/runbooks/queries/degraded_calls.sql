-- Model provider outage or degraded runs: first check (read only, operator role, identifiers and counts only).
SELECT task_kind, count(*) FILTER (WHERE degraded) AS degraded, count(*) AS calls FROM llm_calls WHERE created_at > now() - interval '1 hour' GROUP BY task_kind ORDER BY task_kind;
