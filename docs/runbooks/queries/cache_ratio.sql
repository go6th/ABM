-- Cache-read ratio below 70%: first check (read only, operator role, identifiers and counts only).
SELECT task_kind, sum(cache_read_tokens) AS read, sum(cache_read_tokens + cache_write_tokens + greatest(input_tokens - cache_read_tokens - cache_write_tokens, 0)) AS total FROM llm_calls WHERE created_at > now() - interval '1 hour' GROUP BY task_kind ORDER BY task_kind;
