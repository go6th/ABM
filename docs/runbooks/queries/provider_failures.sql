-- Provider outage or open circuit: first check (read only, operator role, identifiers and counts only).
SELECT provider_code, count(*) FILTER (WHERE event = 'failed') AS failed, count(*) FILTER (WHERE event = 'done') AS done FROM tool_call_events WHERE created_at > now() - interval '1 hour' GROUP BY provider_code ORDER BY provider_code;
