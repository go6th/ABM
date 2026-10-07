-- Migration 0029: signal monitors, dedupe and scoring (Phase 4 WS3, WS4; expand only). Spec
-- docs/prompts/build_phase4.md s5, SM1 to SM4, DS1 to DS3, D-73, D-75. down_revision 0028.
--
-- monitor_configs: insert-only versions of an account's monitor set (kinds on or off).
-- monitor_runs: insert-only; one poll of one monitor kind for one account, with its outcome
--   (ok, blocked with a reason, not_configured) and the units it reserved.
-- webhook_receipts: insert-only; unique (source, event_id): a replayed delivery is visible.
--   The payload is stored as a quarantine reference only, never interpreted here.
-- signal_events: one row per (account, fingerprint), unique: the same event from a second
--   outlet, a re-fetch, a replay or a later probe attaches evidence instead. Score, components
--   and config version stored with it. A Lead is flagged.
-- signal_event_evidence: insert-only; each source that reported the event.
-- signal_event_links: insert-only; ambiguous pairs, linked and delivered together, never merged.
-- intake_drops: insert-only; a candidate dropped with a counted reason (never promoted).
-- Rollback: 0029_signals.down.sql.

CREATE TABLE monitor_configs (
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   account_id   uuid NOT NULL,
   version      integer NOT NULL,
   kinds        text[] NOT NULL,
   set_by       text NOT NULL,
   created_at   timestamptz NOT NULL,
   CONSTRAINT pk_monitor_configs PRIMARY KEY (tenant_id, account_id, version),
   CONSTRAINT fk_monitor_configs_account FOREIGN KEY (tenant_id, account_id)
      REFERENCES accounts (tenant_id, id),
   CONSTRAINT ck_monitor_configs_kinds CHECK (kinds <@ ARRAY['build_role_postings',
      'leadership_changes', 'news_events', 'tech_detection_changes', 'facility_expansions',
      'vendor_customer_stories']::text[] AND version >= 1)
);
ALTER TABLE monitor_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE monitor_configs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON monitor_configs TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON monitor_configs TO app_rw;

CREATE TABLE monitor_runs (
   id           uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   account_id   uuid NOT NULL,
   kind         text NOT NULL,
   refresh_run_id uuid,
   outcome      text NOT NULL,
   reason       text,
   observations integer NOT NULL DEFAULT 0,
   units        integer NOT NULL DEFAULT 0,
   at           timestamptz NOT NULL,
   CONSTRAINT pk_monitor_runs PRIMARY KEY (id),
   CONSTRAINT fk_monitor_runs_account FOREIGN KEY (tenant_id, account_id)
      REFERENCES accounts (tenant_id, id),
   CONSTRAINT ck_monitor_runs_outcome CHECK (outcome IN ('ok', 'blocked', 'not_configured',
      'throttled')),
   CONSTRAINT ck_monitor_runs_reason CHECK ((outcome = 'ok') = (reason IS NULL)
      AND (reason IS NULL OR reason ~ '^[a-z_]{1,40}$')),
   CONSTRAINT ck_monitor_runs_counts CHECK (observations >= 0 AND units >= 0)
);
CREATE INDEX ix_monitor_runs_account ON monitor_runs (tenant_id, account_id, at);
ALTER TABLE monitor_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE monitor_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON monitor_runs TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON monitor_runs TO app_rw;

CREATE TABLE webhook_receipts (
   id           bigserial NOT NULL,
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   source       text NOT NULL,
   event_id     text NOT NULL,
   verdict      text NOT NULL,
   payload_ref  text,
   received_at  timestamptz NOT NULL,
   CONSTRAINT pk_webhook_receipts PRIMARY KEY (id),
   CONSTRAINT fk_webhook_receipts_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT uq_webhook_receipts_event UNIQUE (tenant_id, source, event_id),
   CONSTRAINT ck_webhook_receipts_verdict CHECK (verdict IN ('ok', 'replay')),
   CONSTRAINT ck_webhook_receipts_source CHECK (source ~ '^p_[a-z]+$'),
   CONSTRAINT ck_webhook_receipts_event CHECK (char_length(event_id) BETWEEN 1 AND 200)
);
ALTER TABLE webhook_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON webhook_receipts TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON webhook_receipts TO app_rw;
GRANT USAGE ON SEQUENCE webhook_receipts_id_seq TO app_rw;

CREATE TABLE signal_events (
   id              uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id       uuid NOT NULL DEFAULT app_tenant(),
   account_id      uuid NOT NULL,
   fingerprint     text NOT NULL,
   signal_type     text NOT NULL,
   event_type      text NOT NULL,
   entity          text NOT NULL,
   title           text NOT NULL,
   bucket          integer NOT NULL,
   grade           text NOT NULL,
   lead            boolean NOT NULL,
   sensitive       boolean NOT NULL,
   fact_id         uuid NOT NULL,
   delta_item_id   uuid,
   score           integer NOT NULL,
   components      jsonb NOT NULL,
   config_version  text NOT NULL,
   detected_at     timestamptz NOT NULL,
   CONSTRAINT pk_signal_events PRIMARY KEY (id),
   CONSTRAINT fk_signal_events_account FOREIGN KEY (tenant_id, account_id)
      REFERENCES accounts (tenant_id, id),
   CONSTRAINT fk_signal_events_fact FOREIGN KEY (fact_id) REFERENCES facts (id),
   CONSTRAINT fk_signal_events_delta FOREIGN KEY (delta_item_id) REFERENCES delta_items (id),
   CONSTRAINT uq_signal_events_fingerprint UNIQUE (tenant_id, account_id, fingerprint),
   CONSTRAINT ck_signal_events_fingerprint CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_signal_events_grade CHECK (grade IN ('confirmed', 'probable', 'hypothesis',
      'lead')),
   CONSTRAINT ck_signal_events_lead CHECK (lead = (grade = 'lead') OR lead),
   CONSTRAINT ck_signal_events_score CHECK (score >= 0),
   CONSTRAINT ck_signal_events_text CHECK (char_length(entity) BETWEEN 1 AND 300
      AND char_length(title) BETWEEN 1 AND 500),
   CONSTRAINT ck_signal_events_components CHECK (jsonb_typeof(components) = 'array')
);
CREATE INDEX ix_signal_events_detected ON signal_events (tenant_id, detected_at, id);
ALTER TABLE signal_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE signal_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON signal_events TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON signal_events TO app_rw;

CREATE TABLE signal_event_evidence (
   id            bigserial NOT NULL,
   tenant_id     uuid NOT NULL DEFAULT app_tenant(),
   event_id      uuid NOT NULL,
   source_code   text,
   source_tier   text NOT NULL,
   url_sha256    text NOT NULL,
   via           text NOT NULL,
   at            timestamptz NOT NULL,
   CONSTRAINT pk_signal_event_evidence PRIMARY KEY (id),
   CONSTRAINT fk_signal_event_evidence_event FOREIGN KEY (event_id) REFERENCES signal_events (id),
   CONSTRAINT uq_signal_event_evidence_url UNIQUE (event_id, url_sha256),
   CONSTRAINT ck_signal_event_evidence_tier CHECK (source_tier IN ('t1', 't2', 't3')),
   CONSTRAINT ck_signal_event_evidence_url CHECK (url_sha256 ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_signal_event_evidence_via CHECK (via IN ('poll', 'webhook', 'refresh')),
   CONSTRAINT ck_signal_event_evidence_code CHECK (source_code IS NULL
      OR source_code ~ '^S-[0-9]{4,}$')
);
ALTER TABLE signal_event_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE signal_event_evidence FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON signal_event_evidence TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON signal_event_evidence TO app_rw;
GRANT USAGE ON SEQUENCE signal_event_evidence_id_seq TO app_rw;

CREATE TABLE signal_event_links (
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   event_a      uuid NOT NULL,
   event_b      uuid NOT NULL,
   relation     text NOT NULL,
   at           timestamptz NOT NULL,
   CONSTRAINT pk_signal_event_links PRIMARY KEY (event_a, event_b),
   CONSTRAINT fk_signal_event_links_a FOREIGN KEY (event_a) REFERENCES signal_events (id),
   CONSTRAINT fk_signal_event_links_b FOREIGN KEY (event_b) REFERENCES signal_events (id),
   CONSTRAINT ck_signal_event_links_distinct CHECK (event_a <> event_b),
   CONSTRAINT ck_signal_event_links_relation CHECK (relation = 'ambiguous')
);
-- one link per unordered pair
CREATE UNIQUE INDEX uq_signal_event_links_pair ON signal_event_links
   (LEAST(event_a, event_b), GREATEST(event_a, event_b));
ALTER TABLE signal_event_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE signal_event_links FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON signal_event_links TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON signal_event_links TO app_rw;

CREATE TABLE intake_drops (
   id           bigserial NOT NULL,
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   account_id   uuid NOT NULL,
   kind         text NOT NULL,
   reason       text NOT NULL,
   at           timestamptz NOT NULL,
   CONSTRAINT pk_intake_drops PRIMARY KEY (id),
   CONSTRAINT fk_intake_drops_account FOREIGN KEY (tenant_id, account_id)
      REFERENCES accounts (tenant_id, id),
   CONSTRAINT ck_intake_drops_reason CHECK (reason IN ('no_evidence', 'unverifiable',
      'bad_signature', 'stale', 'unknown_source', 'replay', 'unsafe_url', 'not_relevant'))
);
ALTER TABLE intake_drops ENABLE ROW LEVEL SECURITY;
ALTER TABLE intake_drops FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON intake_drops TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON intake_drops TO app_rw;
GRANT USAGE ON SEQUENCE intake_drops_id_seq TO app_rw;
