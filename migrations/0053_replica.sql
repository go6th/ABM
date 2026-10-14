-- The product as designed (REPLICA_PROMPT.md, owner decision 2026-10-14): client limits and
-- features, the monthly credit pool, research jobs (new research, web refresh, scheduled
-- refresh), schedules on accounts, notifications, user access state, question categories, the
-- 6th Library and the nightly isolation check. Every tenant table has forced row level security
-- and an entry in tests/isolation/matrix/matrix.yaml.

-- ---------------------------------------------------------------------------------------------
-- Platform defaults (one row; written by the operator plane only).
CREATE TABLE platform_defaults (
   id                     smallint NOT NULL DEFAULT 1,
   account_cap_per_user   integer NOT NULL DEFAULT 34,
   idle_expiry_days       integer NOT NULL DEFAULT 7,
   monthly_credit_pool    integer NOT NULL DEFAULT 2000,
   fastest_schedule       text NOT NULL DEFAULT '3d',
   credit_cap_per_pull    integer NOT NULL DEFAULT 400,
   model_split            text NOT NULL DEFAULT 'split',
   skill_version          text,
   updated_by             text NOT NULL DEFAULT 'migration',
   updated_at             timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_platform_defaults PRIMARY KEY (id),
   CONSTRAINT ck_platform_defaults_one CHECK (id = 1),
   CONSTRAINT ck_platform_defaults_cap CHECK (account_cap_per_user BETWEEN 1 AND 10000),
   CONSTRAINT ck_platform_defaults_idle CHECK (idle_expiry_days IN (7, 10, 14, 30)),
   CONSTRAINT ck_platform_defaults_pool CHECK (monthly_credit_pool BETWEEN 0 AND 1000000),
   CONSTRAINT ck_platform_defaults_fastest CHECK (fastest_schedule IN ('3d', 'weekly', '2w')),
   CONSTRAINT ck_platform_defaults_pull CHECK (credit_cap_per_pull BETWEEN 1 AND 100000),
   CONSTRAINT ck_platform_defaults_split CHECK (model_split IN ('split', 'single'))
);
INSERT INTO platform_defaults (id) VALUES (1);
GRANT SELECT, UPDATE ON platform_defaults TO app_rw;

-- ---------------------------------------------------------------------------------------------
-- Client settings: limits per user and features. One row per tenant, created from the platform
-- defaults when first read.
CREATE TABLE client_settings (
   tenant_id              uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   account_cap_per_user   integer NOT NULL,
   idle_expiry_days       integer NOT NULL,
   monthly_credit_pool    integer NOT NULL,
   fastest_schedule       text NOT NULL,
   mail_drive_allowed     boolean NOT NULL DEFAULT true,
   web_refresh_allowed    boolean NOT NULL DEFAULT true,
   status                 text NOT NULL DEFAULT 'active',
   updated_by             text NOT NULL,
   updated_at             timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_client_settings PRIMARY KEY (tenant_id),
   CONSTRAINT ck_client_settings_cap CHECK (account_cap_per_user BETWEEN 1 AND 10000),
   CONSTRAINT ck_client_settings_idle CHECK (idle_expiry_days IN (7, 10, 14, 30)),
   CONSTRAINT ck_client_settings_pool CHECK (monthly_credit_pool BETWEEN 0 AND 1000000),
   CONSTRAINT ck_client_settings_fastest CHECK (fastest_schedule IN ('3d', 'weekly', '2w', 'monthly')),
   CONSTRAINT ck_client_settings_status CHECK (status IN ('invited', 'active'))
);
ALTER TABLE client_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_settings FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON client_settings TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE ON client_settings TO app_rw;

-- ---------------------------------------------------------------------------------------------
-- Accounts: who added it (the account cap counts per user), how it arrived, its lanes and its
-- schedule.
ALTER TABLE accounts ADD COLUMN added_by text;
ALTER TABLE accounts ADD COLUMN origin text NOT NULL DEFAULT 'research';
ALTER TABLE accounts ADD COLUMN lanes text[] NOT NULL DEFAULT '{}';
ALTER TABLE accounts ADD COLUMN next_refresh_at timestamptz;
ALTER TABLE accounts ADD COLUMN refresh_scope text NOT NULL DEFAULT 'full';
ALTER TABLE accounts ADD COLUMN library_account_id uuid;
ALTER TABLE accounts ADD CONSTRAINT ck_accounts_origin CHECK (origin IN ('research', 'library', 'file'));
ALTER TABLE accounts ADD CONSTRAINT ck_accounts_refresh_scope CHECK (refresh_scope IN ('jobs', 'people', 'full'));
ALTER TABLE accounts ADD CONSTRAINT ck_accounts_cadence CHECK (
   refresh_cadence IS NULL OR refresh_cadence IN ('3d', 'weekly', '2w', 'monthly', 'off')) NOT VALID;
CREATE INDEX ix_accounts_tenant_added_by ON accounts (tenant_id, added_by);
CREATE INDEX ix_accounts_next_refresh ON accounts (next_refresh_at) WHERE next_refresh_at IS NOT NULL;

-- ---------------------------------------------------------------------------------------------
-- Research jobs: what a user (or the scheduler) asked for, with its credits. The engine's run is
-- account_runs; a job waiting for credits has no run yet.
CREATE TABLE research_jobs (
   id                 uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id          uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   account_id         uuid NOT NULL,
   kind               text NOT NULL,
   scope              text NOT NULL,
   status             text NOT NULL,
   period             text NOT NULL,
   credits_estimated  integer NOT NULL,
   credits_used       integer NOT NULL DEFAULT 0,
   run_id             uuid,
   from_turn_id       uuid,
   requested_by       text NOT NULL,
   failure_reason     text,
   created_at         timestamptz NOT NULL DEFAULT now(),
   started_at         timestamptz,
   finished_at        timestamptz,
   CONSTRAINT pk_research_jobs PRIMARY KEY (id),
   CONSTRAINT uq_research_jobs_tenant_id UNIQUE (tenant_id, id),
   CONSTRAINT fk_research_jobs_account FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id) ON DELETE CASCADE,
   CONSTRAINT ck_research_jobs_kind CHECK (kind IN ('new_research', 'web_refresh', 'scheduled_refresh', 'file_analysis')),
   CONSTRAINT ck_research_jobs_scope CHECK (scope IN ('jobs', 'people', 'full', 'file')),
   CONSTRAINT ck_research_jobs_status CHECK (status IN ('waiting_credit_limit', 'queued', 'running', 'done', 'failed', 'canceled')),
   CONSTRAINT ck_research_jobs_period CHECK (period ~ '^[0-9]{4}-[0-9]{2}$'),
   CONSTRAINT ck_research_jobs_credits CHECK (credits_estimated >= 0 AND credits_used >= 0),
   CONSTRAINT ck_research_jobs_finished CHECK ((status IN ('done', 'failed', 'canceled')) = (finished_at IS NOT NULL)),
   CONSTRAINT ck_research_jobs_reason CHECK (failure_reason IS NULL OR char_length(failure_reason) <= 400)
);
ALTER TABLE research_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON research_jobs TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE ON research_jobs TO app_rw;
CREATE INDEX ix_research_jobs_account ON research_jobs (tenant_id, account_id, created_at DESC);
CREATE INDEX ix_research_jobs_open ON research_jobs (tenant_id, status) WHERE status IN ('waiting_credit_limit', 'queued', 'running');

-- ---------------------------------------------------------------------------------------------
-- The monthly credit pool, as an append-only ledger. Held = reserve - release; used = settle;
-- left = pool - used - held, per period (YYYY-MM, the period the job was confirmed in).
CREATE TABLE credit_entries (
   id         bigint GENERATED BY DEFAULT AS IDENTITY,
   tenant_id  uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   period     text NOT NULL,
   kind       text NOT NULL,
   credits    integer NOT NULL,
   job_id     uuid NOT NULL,
   purpose    text NOT NULL,
   actor      text NOT NULL,
   at         timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_credit_entries PRIMARY KEY (id),
   CONSTRAINT fk_credit_entries_job FOREIGN KEY (tenant_id, job_id) REFERENCES research_jobs (tenant_id, id) ON DELETE CASCADE,
   CONSTRAINT uq_credit_entries_once UNIQUE (tenant_id, job_id, kind),
   CONSTRAINT ck_credit_entries_period CHECK (period ~ '^[0-9]{4}-[0-9]{2}$'),
   CONSTRAINT ck_credit_entries_kind CHECK (kind IN ('reserve', 'release', 'settle')),
   CONSTRAINT ck_credit_entries_credits CHECK (credits >= 0),
   CONSTRAINT ck_credit_entries_purpose CHECK (purpose IN ('new_research', 'web_refresh', 'scheduled_refresh', 'file_analysis'))
);
ALTER TABLE credit_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_entries FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON credit_entries TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON credit_entries TO app_rw;
CREATE INDEX ix_credit_entries_period ON credit_entries (tenant_id, period);

-- ---------------------------------------------------------------------------------------------
-- In-app notifications for one user: a refresh finished, a run waits for credits.
CREATE TABLE notifications (
   id          uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id   uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   user_id     text NOT NULL,
   kind        text NOT NULL,
   account_id  uuid,
   job_id      uuid,
   created_at  timestamptz NOT NULL DEFAULT now(),
   read_at     timestamptz,
   CONSTRAINT pk_notifications PRIMARY KEY (id),
   CONSTRAINT ck_notifications_kind CHECK (kind IN ('refresh_finished', 'research_finished', 'research_failed', 'waiting_credit_limit', 'access_expiring', 'library_added'))
);
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON notifications TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE ON notifications TO app_rw;
CREATE INDEX ix_notifications_user ON notifications (tenant_id, user_id, created_at DESC);

-- ---------------------------------------------------------------------------------------------
-- User access: active, expired after idle days, or terminated by an admin. History is kept.
ALTER TABLE memberships ADD COLUMN access_state text NOT NULL DEFAULT 'active';
ALTER TABLE memberships ADD COLUMN last_active_at timestamptz;
ALTER TABLE memberships ADD COLUMN ended_at timestamptz;
ALTER TABLE memberships ADD CONSTRAINT ck_memberships_access_state CHECK (access_state IN ('active', 'expired', 'terminated'));
ALTER TABLE memberships ADD CONSTRAINT ck_memberships_ended CHECK ((access_state = 'active') = (ended_at IS NULL));

-- Password sign-in only for members whose access is active (the definer's owner may read the
-- new column and nothing else new).
GRANT SELECT (access_state) ON memberships TO identity_authenticator;
CREATE OR REPLACE FUNCTION local_auth_memberships(p_user_id uuid)
   RETURNS TABLE (tenant_id uuid, user_id text, role text)
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT m.tenant_id, m.user_id, m.role
     FROM memberships m
     JOIN users tu ON tu.tenant_id = m.tenant_id AND tu.subject = m.user_id
     JOIN local_identities u ON u.id = p_user_id
    WHERE u.status = 'active' AND u.email_verified_at IS NOT NULL AND m.status = 'active'
      AND m.access_state = 'active'
      AND lower(btrim(tu.email)) = u.email
      AND NOT COALESCE((
         SELECT e.event = 'suspended' FROM tenant_events e
          WHERE e.tenant_id = m.tenant_id AND e.event IN ('suspended', 'reactivated')
          ORDER BY e.seq DESC LIMIT 1), false)
    ORDER BY m.tenant_id
$$;

-- ---------------------------------------------------------------------------------------------
-- Question categories: counts only, per client per week. Question text never leaves the tenant.
CREATE TABLE question_categories (
   tenant_id  uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   week       date NOT NULL,
   category   text NOT NULL,
   questions  integer NOT NULL DEFAULT 0,
   CONSTRAINT pk_question_categories PRIMARY KEY (tenant_id, week, category),
   CONSTRAINT ck_question_categories_category CHECK (category IN ('people', 'hiring', 'first_call', 'technology', 'changes', 'other')),
   CONSTRAINT ck_question_categories_count CHECK (questions >= 0)
);
ALTER TABLE question_categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE question_categories FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON question_categories TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE ON question_categories TO app_rw;

-- ---------------------------------------------------------------------------------------------
-- Chat answers: what each answer cited and used, so a reopened thread shows the same panel.
ALTER TABLE chat_turns ADD COLUMN answer_json jsonb;
ALTER TABLE chat_turns ADD CONSTRAINT ck_chat_turns_answer_json CHECK (answer_json IS NULL OR jsonb_typeof(answer_json) = 'object');

-- ---------------------------------------------------------------------------------------------
-- The 6th Library: platform content (not tenant data). An upload holds one or more account
-- workspaces; each is checked for client readiness; a publication lets one client add it.
CREATE TABLE library_uploads (
   id           uuid NOT NULL DEFAULT gen_random_uuid(),
   filename     text NOT NULL,
   size_bytes   bigint NOT NULL,
   sha256       text NOT NULL,
   object_key   text NOT NULL,
   uploaded_by  text NOT NULL,
   created_at   timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_library_uploads PRIMARY KEY (id),
   CONSTRAINT ck_library_uploads_name CHECK (char_length(filename) BETWEEN 1 AND 255),
   CONSTRAINT ck_library_uploads_sha CHECK (sha256 ~ '^[0-9a-f]{64}$')
);
GRANT SELECT, INSERT ON library_uploads TO app_rw;

CREATE TABLE library_accounts (
   id                  uuid NOT NULL DEFAULT gen_random_uuid(),
   upload_id           uuid NOT NULL REFERENCES library_uploads (id),
   name                text NOT NULL,
   domain              text NOT NULL,
   deliverables_found  integer NOT NULL,
   internal_left_out   boolean NOT NULL DEFAULT false,
   check_passed        boolean NOT NULL,
   issues              jsonb NOT NULL DEFAULT '[]',
   content             jsonb NOT NULL,
   files               jsonb NOT NULL DEFAULT '[]',
   superseded_at       timestamptz,
   created_at          timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_library_accounts PRIMARY KEY (id),
   CONSTRAINT ck_library_accounts_found CHECK (deliverables_found BETWEEN 0 AND 5),
   CONSTRAINT ck_library_accounts_issues CHECK (jsonb_typeof(issues) = 'array'),
   CONSTRAINT ck_library_accounts_content CHECK (jsonb_typeof(content) = 'object'),
   CONSTRAINT ck_library_accounts_files CHECK (jsonb_typeof(files) = 'array')
);

-- Which client may add which library account. Tenant rows; written by the operator plane
-- inside the target tenant's transaction.
CREATE TABLE library_publications (
   tenant_id           uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   library_account_id  uuid NOT NULL REFERENCES library_accounts (id),
   published_by        text NOT NULL,
   published_at        timestamptz NOT NULL DEFAULT now(),
   revoked_at          timestamptz,
   CONSTRAINT pk_library_publications PRIMARY KEY (tenant_id, library_account_id)
);
ALTER TABLE library_publications ENABLE ROW LEVEL SECURITY;
ALTER TABLE library_publications FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON library_publications TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE ON library_publications TO app_rw;

-- A client sees a library account only while it is published to that client. The operator plane
-- (no tenant context) sees every library account.
ALTER TABLE library_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE library_accounts FORCE ROW LEVEL SECURITY;
CREATE POLICY library_visible ON library_accounts FOR SELECT TO app_rw
   USING ((SELECT app_tenant()) IS NULL OR EXISTS (
      SELECT 1 FROM library_publications p
       WHERE p.library_account_id = library_accounts.id
         AND p.tenant_id = (SELECT app_tenant()) AND p.revoked_at IS NULL));
CREATE POLICY library_write ON library_accounts FOR INSERT TO app_rw
   WITH CHECK ((SELECT app_tenant()) IS NULL);
CREATE POLICY library_update ON library_accounts FOR UPDATE TO app_rw
   USING ((SELECT app_tenant()) IS NULL) WITH CHECK ((SELECT app_tenant()) IS NULL);
GRANT SELECT, INSERT, UPDATE ON library_accounts TO app_rw;

-- ---------------------------------------------------------------------------------------------
-- The nightly isolation check (platform): one row per pass.
CREATE TABLE isolation_checks (
   id                  uuid NOT NULL DEFAULT gen_random_uuid(),
   ran_at              timestamptz NOT NULL DEFAULT now(),
   passed              boolean NOT NULL,
   workspaces_checked  integer NOT NULL,
   failures            integer NOT NULL DEFAULT 0,
   detail              text,
   CONSTRAINT pk_isolation_checks PRIMARY KEY (id),
   CONSTRAINT ck_isolation_checks_counts CHECK (workspaces_checked >= 0 AND failures >= 0),
   CONSTRAINT ck_isolation_checks_detail CHECK (detail IS NULL OR char_length(detail) <= 2000)
);
GRANT SELECT, INSERT ON isolation_checks TO app_rw;

-- ---------------------------------------------------------------------------------------------
-- Mail and drive: per-user connections and the items matched to that user's accounts. Items are
-- private to the user who connected them and never enter shareable deliverables.
ALTER TABLE connector_connections ADD COLUMN owner_user_id text;
ALTER TABLE connector_connections ADD COLUMN provider text;
ALTER TABLE connector_connections ADD COLUMN last_synced_at timestamptz;
ALTER TABLE connector_items ADD COLUMN owner_user_id text;
ALTER TABLE connector_items ADD COLUMN account_id uuid;
ALTER TABLE connector_items ADD COLUMN item_kind text;
ALTER TABLE connector_items ADD COLUMN title text;
ALTER TABLE connector_items ADD COLUMN snippet text;
ALTER TABLE connector_items ADD COLUMN item_date timestamptz;
ALTER TABLE connector_items ADD COLUMN location text;
ALTER TABLE connector_items ADD CONSTRAINT ck_connector_items_kind CHECK (item_kind IS NULL OR item_kind IN ('thread', 'file'));
ALTER TABLE connector_items ADD CONSTRAINT ck_connector_items_snippet CHECK (snippet IS NULL OR char_length(snippet) <= 2000);
CREATE INDEX ix_connector_items_owner ON connector_items (tenant_id, owner_user_id, account_id);
ALTER TABLE connector_connections DROP CONSTRAINT ck_connector_connections_kind;
ALTER TABLE connector_connections ADD CONSTRAINT ck_connector_connections_kind CHECK (kind IN (
   'google_drive', 'salesforce', 'hubspot', 'gmail', 'call_notes', 'outlook', 'onedrive', 'sharepoint'));
ALTER TABLE connector_items DROP CONSTRAINT ck_connector_items_state;
ALTER TABLE connector_items ADD CONSTRAINT ck_connector_items_state CHECK (state IN ('quarantined', 'matched'));
