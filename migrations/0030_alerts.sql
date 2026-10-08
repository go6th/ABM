-- Migration 0030: alerting (Phase 4 WS5; expand only). Spec docs/phase4/alert_catalog.md,
-- AL1 to AL8, D-74 to D-76. down_revision 0029.
--
-- alerts: one per (rule, account, fingerprint), unique (DS1); fact_delta_id NOT NULL and a foreign
--   key to delta_items: an alert without a graded, sourced delta cannot exist (AL1). Insert-only.
--   The budget rule is not a fact delta (C29): budget notices use notification_outbox instead.
-- alert_outbox: delivery per (alert, channel, recipient), unique, written in the alert's
--   transaction. Mutable delivery state (documented): pending -> sent | failed, attempts and the
--   next attempt by conditional updates; the outbox id is the delivery event id.
-- alert_suppressions: insert-only; an alert (or event) not delivered to a user, with the reason.
-- subscriptions: insert-only versions per user (subscribe up: absent means digest only).
-- tenant_rules, tenant_rule_accounts: a tenant's own rules; accounts referenced by a composite
--   foreign key to (tenant_id, account id), so a rule can never name another tenant's account.
-- alert_feedback: insert-only ratings (useful, not useful, fixed reason); newest per user wins.
-- digest_runs: one digest per user and week, unique.
-- Rollback: 0030_alerts.down.sql.

CREATE TABLE alerts (
   id                  uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id           uuid NOT NULL DEFAULT app_tenant(),
   account_id          uuid NOT NULL,
   rule_id             text NOT NULL,
   fingerprint         text NOT NULL,
   event_id            uuid NOT NULL,
   fact_delta_id       uuid NOT NULL,
   tier                text NOT NULL,
   score               integer NOT NULL,
   lead                boolean NOT NULL,
   subject             text NOT NULL,
   body                text NOT NULL,
   signal_detected_at  timestamptz NOT NULL,
   created_at          timestamptz NOT NULL,
   CONSTRAINT pk_alerts PRIMARY KEY (id),
   CONSTRAINT fk_alerts_account FOREIGN KEY (tenant_id, account_id)
      REFERENCES accounts (tenant_id, id),
   CONSTRAINT fk_alerts_event FOREIGN KEY (event_id) REFERENCES signal_events (id),
   CONSTRAINT fk_alerts_delta FOREIGN KEY (fact_delta_id) REFERENCES delta_items (id),
   CONSTRAINT uq_alerts_dedupe UNIQUE (tenant_id, rule_id, account_id, fingerprint),
   CONSTRAINT ck_alerts_rule CHECK (rule_id ~ '^[a-z_0-9:-]{1,80}$' AND rule_id <> 'budget'),
   CONSTRAINT ck_alerts_tier CHECK (tier IN ('immediate', 'digest')),
   CONSTRAINT ck_alerts_lead CHECK (NOT lead OR tier = 'digest'),
   CONSTRAINT ck_alerts_text CHECK (char_length(subject) BETWEEN 1 AND 200
      AND char_length(body) BETWEEN 1 AND 4000)
);
CREATE INDEX ix_alerts_created ON alerts (tenant_id, created_at, id);
ALTER TABLE alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE alerts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON alerts TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON alerts TO app_rw;

CREATE TABLE alert_outbox (
   id               uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id        uuid NOT NULL DEFAULT app_tenant(),
   alert_id         uuid NOT NULL,
   channel          text NOT NULL,
   recipient        text NOT NULL,
   status           text NOT NULL DEFAULT 'pending',
   attempts         integer NOT NULL DEFAULT 0,
   next_attempt_at  timestamptz NOT NULL,
   delivered_at     timestamptz,
   last_error       text,
   CONSTRAINT pk_alert_outbox PRIMARY KEY (id),
   CONSTRAINT fk_alert_outbox_alert FOREIGN KEY (alert_id) REFERENCES alerts (id),
   CONSTRAINT uq_alert_outbox_delivery UNIQUE (alert_id, channel, recipient),
   CONSTRAINT ck_alert_outbox_channel CHECK (channel IN ('in_app', 'email', 'slack', 'webhook',
      'digest')),
   CONSTRAINT ck_alert_outbox_status CHECK (status IN ('pending', 'sent', 'failed')),
   CONSTRAINT ck_alert_outbox_sent CHECK ((status = 'sent') = (delivered_at IS NOT NULL)),
   CONSTRAINT ck_alert_outbox_error CHECK (last_error IS NULL OR last_error ~ '^[a-z_]{1,40}$'),
   CONSTRAINT ck_alert_outbox_attempts CHECK (attempts >= 0)
);
CREATE INDEX ix_alert_outbox_due ON alert_outbox (status, next_attempt_at);
ALTER TABLE alert_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON alert_outbox TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON alert_outbox TO app_rw;
GRANT UPDATE (status, attempts, next_attempt_at, delivered_at, last_error) ON alert_outbox
   TO app_rw;

CREATE TABLE alert_suppressions (
   id          bigserial NOT NULL,
   tenant_id   uuid NOT NULL DEFAULT app_tenant(),
   event_id    uuid NOT NULL,
   rule_id     text NOT NULL,
   recipient   text NOT NULL,
   reason      text NOT NULL,
   at          timestamptz NOT NULL,
   CONSTRAINT pk_alert_suppressions PRIMARY KEY (id),
   CONSTRAINT fk_alert_suppressions_event FOREIGN KEY (event_id) REFERENCES signal_events (id),
   CONSTRAINT ck_alert_suppressions_reason CHECK (reason IN ('muted', 'snoozed',
      'below_threshold', 'not_subscribed', 'account_news_cap', 'user_hourly_cap',
      'user_daily_cap', 'digest_threshold', 'lead_not_verified', 'below_grade_floor',
      'needs_two_sources', 'no_evidence'))
);
ALTER TABLE alert_suppressions ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_suppressions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON alert_suppressions TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON alert_suppressions TO app_rw;
GRANT USAGE ON SEQUENCE alert_suppressions_id_seq TO app_rw;

CREATE TABLE subscriptions (
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   user_id      text NOT NULL,
   version      integer NOT NULL,
   settings     jsonb NOT NULL,
   set_by       text NOT NULL,
   bulk         boolean NOT NULL DEFAULT false,
   created_at   timestamptz NOT NULL,
   CONSTRAINT pk_subscriptions PRIMARY KEY (tenant_id, user_id, version),
   CONSTRAINT fk_subscriptions_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_subscriptions_settings CHECK (jsonb_typeof(settings) = 'object'
      AND octet_length(settings::text) <= 16384 AND version >= 1)
);
ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscriptions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON subscriptions TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON subscriptions TO app_rw;

CREATE TABLE tenant_rules (
   id            uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id     uuid NOT NULL DEFAULT app_tenant(),
   name          text NOT NULL,
   signal_type   text NOT NULL,
   grade_floor   text NOT NULL,
   min_score     integer NOT NULL,
   created_by    text NOT NULL,
   created_at    timestamptz NOT NULL,
   CONSTRAINT pk_tenant_rules PRIMARY KEY (id),
   CONSTRAINT uq_tenant_rules_id UNIQUE (tenant_id, id),
   CONSTRAINT fk_tenant_rules_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_tenant_rules_grade CHECK (grade_floor IN ('confirmed', 'probable',
      'hypothesis')),
   CONSTRAINT ck_tenant_rules_signal CHECK (signal_type ~ '^[a-z_]{1,60}$'),
   CONSTRAINT ck_tenant_rules_score CHECK (min_score >= 0),
   CONSTRAINT ck_tenant_rules_name CHECK (char_length(name) BETWEEN 1 AND 80)
);
ALTER TABLE tenant_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_rules FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenant_rules TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON tenant_rules TO app_rw;

CREATE TABLE tenant_rule_accounts (
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   rule_id      uuid NOT NULL,
   account_id   uuid NOT NULL,
   CONSTRAINT pk_tenant_rule_accounts PRIMARY KEY (rule_id, account_id),
   CONSTRAINT fk_tenant_rule_accounts_rule FOREIGN KEY (tenant_id, rule_id)
      REFERENCES tenant_rules (tenant_id, id),
   CONSTRAINT fk_tenant_rule_accounts_account FOREIGN KEY (tenant_id, account_id)
      REFERENCES accounts (tenant_id, id)
);
ALTER TABLE tenant_rule_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_rule_accounts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenant_rule_accounts TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON tenant_rule_accounts TO app_rw;

CREATE TABLE alert_feedback (
   id          bigserial NOT NULL,
   tenant_id   uuid NOT NULL DEFAULT app_tenant(),
   alert_id    uuid NOT NULL,
   user_id     text NOT NULL,
   verdict     text NOT NULL,
   reason      text NOT NULL,
   at          timestamptz NOT NULL,
   CONSTRAINT pk_alert_feedback PRIMARY KEY (id),
   CONSTRAINT fk_alert_feedback_alert FOREIGN KEY (alert_id) REFERENCES alerts (id),
   CONSTRAINT ck_alert_feedback_verdict CHECK (verdict IN ('useful', 'not_useful')),
   CONSTRAINT ck_alert_feedback_reason CHECK (reason ~ '^[a-z_]{1,40}$')
);
ALTER TABLE alert_feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_feedback FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON alert_feedback TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON alert_feedback TO app_rw;
GRANT USAGE ON SEQUENCE alert_feedback_id_seq TO app_rw;

CREATE TABLE digest_runs (
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   user_id      text NOT NULL,
   period       text NOT NULL,
   items        integer NOT NULL,
   more         integer NOT NULL,
   created_at   timestamptz NOT NULL,
   CONSTRAINT pk_digest_runs PRIMARY KEY (tenant_id, user_id, period),
   CONSTRAINT fk_digest_runs_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_digest_runs_period CHECK (period ~ '^[0-9]{4}-W[0-9]{2}$'),
   CONSTRAINT ck_digest_runs_counts CHECK (items >= 0 AND more >= 0)
);
ALTER TABLE digest_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE digest_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON digest_runs TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON digest_runs TO app_rw;
