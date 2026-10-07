-- Recent operator-plane actions (read only, identifiers and reason codes only).
SELECT at, operator, action, target, reason_code FROM operator_audit WHERE at > now() - interval '7 days' ORDER BY id DESC LIMIT 100;
