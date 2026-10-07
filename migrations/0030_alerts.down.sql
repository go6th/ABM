-- Rollback of 0030_alerts.sql (development only; production never contracts in the same release).
DROP TABLE IF EXISTS digest_runs;
DROP TABLE IF EXISTS alert_feedback;
DROP TABLE IF EXISTS tenant_rule_accounts;
DROP TABLE IF EXISTS tenant_rules;
DROP TABLE IF EXISTS subscriptions;
DROP TABLE IF EXISTS alert_suppressions;
DROP TABLE IF EXISTS alert_outbox;
DROP TABLE IF EXISTS alerts;
