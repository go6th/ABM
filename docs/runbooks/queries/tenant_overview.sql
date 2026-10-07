-- Tenant lifecycle first check (read only, operator role, identifiers and counts only; no names or content).
SELECT tenant_id, plan, suspended, members, accounts, live_runs FROM operator_tenant_overview() ORDER BY suspended DESC, created_at DESC LIMIT 50;
