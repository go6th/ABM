-- Limits per user (owner decision 2026-10-14, Phase 4B section 5). Two limits replace the
-- user-facing account cap and credits: researches (a full public research of one account: new
-- research by company name, Refresh from web or a scheduled refresh; default 2) and chats (a
-- question in Ask or a file analysis; default 40). Both reset on the 1st of each month, or never
-- (the admin then raises limits by hand). The credit ledger stays, for Runs and spend only.
--
-- client_settings / platform_defaults: the defaults for a client's users.
-- user_limits: one user's own limits when the admin changed them (NULL: the client default).
-- usage_periods: what a user has used per period ('2026-10', or 'all' when limits never reset);
--   the count and its check are one atomic statement, so two tabs cannot both take the last one.
-- limit_requests: "Request more from admin": the user's in-app request, decided in the console.
-- All three are the user's own (private workspaces); the console reads them as a system job.

ALTER TABLE platform_defaults
   ADD COLUMN research_limit_default integer NOT NULL DEFAULT 2,
   ADD COLUMN chat_limit_default integer NOT NULL DEFAULT 40,
   ADD COLUMN limit_reset text NOT NULL DEFAULT 'monthly',
   ADD CONSTRAINT ck_platform_defaults_research_limit CHECK (research_limit_default BETWEEN 0 AND 100000),
   ADD CONSTRAINT ck_platform_defaults_chat_limit CHECK (chat_limit_default BETWEEN 0 AND 1000000),
   ADD CONSTRAINT ck_platform_defaults_limit_reset CHECK (limit_reset IN ('monthly', 'never'));

ALTER TABLE client_settings
   ADD COLUMN research_limit_default integer NOT NULL DEFAULT 2,
   ADD COLUMN chat_limit_default integer NOT NULL DEFAULT 40,
   ADD COLUMN limit_reset text NOT NULL DEFAULT 'monthly',
   ADD CONSTRAINT ck_client_settings_research_limit CHECK (research_limit_default BETWEEN 0 AND 100000),
   ADD CONSTRAINT ck_client_settings_chat_limit CHECK (chat_limit_default BETWEEN 0 AND 1000000),
   ADD CONSTRAINT ck_client_settings_limit_reset CHECK (limit_reset IN ('monthly', 'never'));

CREATE TABLE user_limits (
   tenant_id       uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   owner_user_id   text NOT NULL DEFAULT app_user(),
   research_limit  integer,
   chat_limit      integer,
   updated_by      text NOT NULL,
   updated_at      timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_user_limits PRIMARY KEY (tenant_id, owner_user_id),
   CONSTRAINT ck_user_limits_research CHECK (research_limit IS NULL OR research_limit BETWEEN 0 AND 100000),
   CONSTRAINT ck_user_limits_chat CHECK (chat_limit IS NULL OR chat_limit BETWEEN 0 AND 1000000)
);

CREATE TABLE usage_periods (
   tenant_id       uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   owner_user_id   text NOT NULL DEFAULT app_user(),
   period          text NOT NULL,
   researches      integer NOT NULL DEFAULT 0,
   chats           integer NOT NULL DEFAULT 0,
   updated_at      timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_usage_periods PRIMARY KEY (tenant_id, owner_user_id, period),
   CONSTRAINT ck_usage_periods_period CHECK (period = 'all' OR period ~ '^[0-9]{4}-[0-9]{2}$'),
   CONSTRAINT ck_usage_periods_counts CHECK (researches >= 0 AND chats >= 0)
);

CREATE TABLE limit_requests (
   id              uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id       uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   owner_user_id   text NOT NULL DEFAULT app_user(),
   kind            text NOT NULL,
   note            text,
   status          text NOT NULL DEFAULT 'pending',
   created_at      timestamptz NOT NULL DEFAULT now(),
   decided_at      timestamptz,
   decided_by      text,
   granted_research_limit integer,
   granted_chat_limit     integer,
   CONSTRAINT pk_limit_requests PRIMARY KEY (id),
   CONSTRAINT uq_limit_requests_tenant_id UNIQUE (tenant_id, id),
   CONSTRAINT ck_limit_requests_kind CHECK (kind IN ('researches', 'chats')),
   CONSTRAINT ck_limit_requests_status CHECK (status IN ('pending', 'approved', 'declined')),
   CONSTRAINT ck_limit_requests_note CHECK (note IS NULL OR char_length(note) <= 500),
   CONSTRAINT ck_limit_requests_decided CHECK ((status = 'pending') = (decided_at IS NULL))
);
-- one open request per user and limit
CREATE UNIQUE INDEX uq_limit_requests_open ON limit_requests (tenant_id, owner_user_id, kind)
   WHERE status = 'pending';
CREATE INDEX ix_limit_requests_tenant_status ON limit_requests (tenant_id, status, created_at);

ALTER TABLE user_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_limits FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_owner_isolation ON user_limits TO app_rw
   USING (tenant_id = (SELECT app_tenant())
          AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'))
   WITH CHECK (tenant_id = (SELECT app_tenant())
          AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'));
GRANT SELECT, INSERT, UPDATE ON user_limits TO app_rw;

ALTER TABLE usage_periods ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_periods FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_owner_isolation ON usage_periods TO app_rw
   USING (tenant_id = (SELECT app_tenant())
          AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'))
   WITH CHECK (tenant_id = (SELECT app_tenant())
          AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'));
GRANT SELECT, INSERT, UPDATE ON usage_periods TO app_rw;

ALTER TABLE limit_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE limit_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_owner_isolation ON limit_requests TO app_rw
   USING (tenant_id = (SELECT app_tenant())
          AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'))
   WITH CHECK (tenant_id = (SELECT app_tenant())
          AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'));
GRANT SELECT, INSERT, UPDATE ON limit_requests TO app_rw;

-- The in-app answer to a request, and a scheduled refresh that the user's limit stopped.
ALTER TABLE notifications DROP CONSTRAINT ck_notifications_kind;
ALTER TABLE notifications ADD CONSTRAINT ck_notifications_kind CHECK (kind IN ('refresh_finished',
   'research_finished', 'research_failed', 'waiting_credit_limit', 'access_expiring',
   'library_added', 'limit_request_approved', 'limit_request_declined', 'research_limit_reached'));
