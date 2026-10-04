-- Migration 0014: people pipeline storage (expand only; no column dropped or altered).
-- Spec docs/prompts/build_people_pipeline.md section 6. Follows 0013 (job gap passes); the
-- down_revision was re-pointed from 0012 to 0013 when the people and jobs branches met.
--
-- `people` (created in 0010: one row per found record per run, insert-only) gains the pipeline
-- columns. Conventions kept from 0010: `profile_url` is set only on a person's canonical row (so
-- uq_people_run_profile still holds), `provider_person_id` carries the stable source record id
-- (the identity of merged and unresolved rows, satisfying ck_people_identity), `job_level` is the
-- level, `excluded` and `excluded_reason` keep their CHECK. Merged and excluded rows are kept.
-- `people` gets a column-level UPDATE grant (merge reversal, rescoping, erasure); there is still
-- no DELETE grant on any people table.
--
-- Append-only tables: person_search_pages, person_sources, person_profiles, person_posts,
-- person_post_pulls, person_events, people_pipeline_runs. The only UPDATE any of them allows is a
-- tombstone erasure (people_erasure_only trigger): personal fields set to NULL with erased_at,
-- every other column unchanged. Raw search pages live in the payload store (page_ref), never in
-- a column. The run record holds no personal data.
-- All tenant tables: tenant_id NOT NULL, forced RLS (USING and WITH CHECK, TO app_rw).
-- Rollback: 0014_people_pipeline.down.sql.

ALTER TABLE people
   ADD COLUMN person_key            text,
   ADD COLUMN source_record_id      text,
   ADD COLUMN origin                text,
   ADD COLUMN headline              text,
   ADD COLUMN location              text,
   ADD COLUMN employer_domain       text,
   ADD COLUMN profile_url_canonical text,
   ADD COLUMN scope_status          text,
   ADD COLUMN scope_reason          text,
   ADD COLUMN rule_id               text,
   ADD COLUMN classification_json   jsonb,
   ADD COLUMN lanes                 text[] NOT NULL DEFAULT '{}',
   ADD COLUMN priority              text,
   ADD COLUMN merged_into           uuid,
   ADD COLUMN possible_duplicate    boolean NOT NULL DEFAULT false,
   ADD COLUMN internal_only         boolean NOT NULL DEFAULT false,
   ADD COLUMN profile_state         text,
   ADD COLUMN profile_failure       text,
   ADD COLUMN posts_requested       boolean NOT NULL DEFAULT false,
   ADD COLUMN activity_unfiltered   boolean NOT NULL DEFAULT false,
   ADD COLUMN fields_dropped        integer NOT NULL DEFAULT 0,
   ADD COLUMN erased_at             timestamptz,
   ADD COLUMN erasure_reason        text,
   ADD CONSTRAINT ck_people_scope_status CHECK (scope_status IS NULL OR scope_status IN
      ('in_scope', 'out_of_scope', 'excluded', 'review', 'unresolved', 'merged')),
   ADD CONSTRAINT ck_people_in_scope_url CHECK (scope_status IS DISTINCT FROM 'in_scope'
      OR profile_url IS NOT NULL),
   ADD CONSTRAINT ck_people_excluded_status CHECK (scope_status IS NULL
      OR (scope_status = 'excluded') = excluded),
   ADD CONSTRAINT ck_people_reason CHECK (scope_status IS NULL
      OR scope_status NOT IN ('out_of_scope', 'excluded', 'review') OR scope_reason IS NOT NULL),
   ADD CONSTRAINT ck_people_level CHECK (job_level IS NULL OR job_level IN
      ('c_level', 'evp', 'svp', 'vp', 'head', 'director', 'senior_manager', 'manager')),
   ADD CONSTRAINT ck_people_priority CHECK (priority IS NULL OR priority IN ('A', 'B', 'C')),
   ADD CONSTRAINT ck_people_priority_in_scope CHECK (scope_status IS NULL
      OR (priority IS NOT NULL) = (scope_status = 'in_scope')),
   ADD CONSTRAINT ck_people_merged CHECK (scope_status IS NULL
      OR (scope_status = 'merged') = (merged_into IS NOT NULL)),
   ADD CONSTRAINT ck_people_not_self CHECK (merged_into IS NULL OR merged_into <> id),
   ADD CONSTRAINT ck_people_origin CHECK (origin IS NULL OR origin IN
      ('title_search', 'association', 'mention')),
   ADD CONSTRAINT ck_people_profile_state CHECK (profile_state IS NULL OR profile_state IN
      ('not_requested', 'read', 'partial', 'failed', 'cap', 'reused')),
   ADD CONSTRAINT ck_people_fields_dropped CHECK (fields_dropped >= 0),
   ADD CONSTRAINT ck_people_erased CHECK (erased_at IS NULL OR (name IS NULL AND title IS NULL
      AND headline IS NULL AND location IS NULL AND erasure_reason IS NOT NULL));
CREATE UNIQUE INDEX uq_people_run_person_key ON people (tenant_id, run_id, person_key)
   WHERE person_key IS NOT NULL;
-- one canonical row per person per run
CREATE UNIQUE INDEX uq_people_run_canonical ON people (tenant_id, run_id, profile_url_canonical)
   WHERE profile_url_canonical IS NOT NULL AND merged_into IS NULL;
CREATE INDEX ix_people_account_id_profile_url_canonical ON people (account_id, profile_url_canonical);
CREATE INDEX ix_people_merged_into ON people (merged_into) WHERE merged_into IS NOT NULL;

-- merged_into must point at a canonical row of the same run (visible to this tenant under RLS),
-- a row others point at cannot itself be merged (no chain, no cycle), and an erased row can never
-- be refilled or un-erased.
CREATE FUNCTION people_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
   target_merged uuid;
BEGIN
   IF TG_OP = 'UPDATE' AND OLD.erased_at IS NOT NULL AND (
         NEW.erased_at IS DISTINCT FROM OLD.erased_at
         OR NEW.name IS NOT NULL OR NEW.title IS NOT NULL
         OR NEW.headline IS NOT NULL OR NEW.location IS NOT NULL) THEN
      RAISE EXCEPTION 'people: an erased row cannot be refilled or un-erased'
         USING ERRCODE = 'check_violation';
   END IF;
   IF NEW.merged_into IS NOT NULL THEN
      SELECT p.merged_into INTO target_merged
        FROM people p WHERE p.id = NEW.merged_into AND p.run_id = NEW.run_id;
      -- FOUND, not a selected flag: SELECT INTO leaves variables NULL when no row matches
      -- (a NULL flag made "IF NOT flag" false and let a dangling or cross-tenant pointer pass)
      IF NOT FOUND THEN
         RAISE EXCEPTION 'people: merged_into must point at a row of the same run'
            USING ERRCODE = 'foreign_key_violation';
      END IF;
      IF target_merged IS NOT NULL THEN
         RAISE EXCEPTION 'people: merged_into must point at a canonical row'
            USING ERRCODE = 'check_violation';
      END IF;
      IF EXISTS (SELECT 1 FROM people p WHERE p.merged_into = NEW.id) THEN
         RAISE EXCEPTION 'people: a row other rows point at cannot be merged'
            USING ERRCODE = 'check_violation';
      END IF;
   END IF;
   RETURN NEW;
END $$;
CREATE TRIGGER trg_people_guard BEFORE INSERT OR UPDATE ON people
   FOR EACH ROW EXECUTE FUNCTION people_guard();
GRANT UPDATE (merged_into, scope_status, scope_reason, rule_id, classification_json, excluded,
   excluded_reason, job_level, lanes, priority, possible_duplicate, profile_state, profile_failure,
   name, title, headline, location, erased_at, erasure_reason) ON people TO app_rw;

-- Append-only with tombstone erasure: TG_ARGV lists the personal columns that erasure nulls.
CREATE FUNCTION people_erasure_only() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
   col      text;
   new_rest jsonb := to_jsonb(NEW) - 'erased_at' - 'erasure_reason';
   old_rest jsonb := to_jsonb(OLD) - 'erased_at' - 'erasure_reason';
BEGIN
   IF OLD.erased_at IS NOT NULL OR NEW.erased_at IS NULL THEN
      RAISE EXCEPTION '%: append-only; the only update allowed is an erasure', TG_TABLE_NAME
         USING ERRCODE = 'insufficient_privilege';
   END IF;
   FOREACH col IN ARRAY TG_ARGV LOOP
      new_rest := new_rest - col;
      old_rest := old_rest - col;
   END LOOP;
   IF new_rest IS DISTINCT FROM old_rest THEN
      RAISE EXCEPTION '%: an erasure may change only personal fields', TG_TABLE_NAME
         USING ERRCODE = 'insufficient_privilege';
   END IF;
   RETURN NEW;
END $$;

CREATE TABLE person_search_pages (
   id          bigserial NOT NULL,
   tenant_id   uuid NOT NULL DEFAULT app_tenant(),
   account_id  uuid NOT NULL,
   run_id      uuid NOT NULL,
   source_id   text NOT NULL,
   page_no     integer NOT NULL,
   cursor_in   text,
   next_cursor text,
   page_ref    text NOT NULL,       -- the raw page in the payload store (neutral key)
   call_ref    text,
   record_count integer NOT NULL,
   complete    boolean NOT NULL,
   created_at  timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_person_search_pages PRIMARY KEY (id),
   CONSTRAINT fk_person_search_pages_tenant_id_account_id_accounts
      FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
   CONSTRAINT uq_person_search_pages_page UNIQUE (tenant_id, run_id, source_id, page_no),
   CONSTRAINT ck_person_search_pages_page_no CHECK (page_no >= 0 AND record_count >= 0)
);
ALTER TABLE person_search_pages ENABLE ROW LEVEL SECURITY;
ALTER TABLE person_search_pages FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON person_search_pages TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON person_search_pages TO app_rw;
GRANT USAGE ON SEQUENCE person_search_pages_id_seq TO app_rw;

CREATE TABLE person_sources (
   id               bigserial NOT NULL,
   tenant_id        uuid NOT NULL DEFAULT app_tenant(),
   account_id       uuid NOT NULL,
   run_id           uuid NOT NULL,
   person_key       text NOT NULL,
   source_record_id text NOT NULL,
   origin           text NOT NULL,
   provider_code    text,            -- internal only; never in a client-safe view
   raw_ref          text,
   call_ref         text,
   lane             text,
   pass_no          integer,
   query            text,
   profile_url_raw  text,
   mention_url      text,
   mention_sentence text,
   fields_dropped   integer NOT NULL DEFAULT 0,
   erased_at        timestamptz,
   erasure_reason   text,
   created_at       timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_person_sources PRIMARY KEY (id),
   CONSTRAINT fk_person_sources_tenant_id_account_id_accounts
      FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
   CONSTRAINT uq_person_sources_record UNIQUE (tenant_id, run_id, source_record_id),
   CONSTRAINT ck_person_sources_origin CHECK (origin IN ('title_search', 'association', 'mention')),
   CONSTRAINT ck_person_sources_erased CHECK (erased_at IS NULL OR (profile_url_raw IS NULL
      AND mention_sentence IS NULL AND erasure_reason IS NOT NULL))
);
ALTER TABLE person_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE person_sources FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON person_sources TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON person_sources TO app_rw;
GRANT UPDATE (profile_url_raw, mention_sentence, erased_at, erasure_reason) ON person_sources TO app_rw;
GRANT USAGE ON SEQUENCE person_sources_id_seq TO app_rw;
CREATE TRIGGER trg_person_sources_erasure_only BEFORE UPDATE ON person_sources
   FOR EACH ROW EXECUTE FUNCTION people_erasure_only('profile_url_raw', 'mention_sentence');

CREATE TABLE person_profiles (
   id                     bigserial NOT NULL,
   tenant_id              uuid NOT NULL DEFAULT app_tenant(),
   account_id             uuid NOT NULL,
   run_id                 uuid NOT NULL,
   profile_url_canonical  text NOT NULL,
   person_key             text NOT NULL,
   provider_code          text NOT NULL,   -- internal only
   raw_ref                text NOT NULL,
   call_ref               text NOT NULL,
   name                   text,
   current_title          text,
   headline               text,
   employer               text,
   prior_employers        text[],
   join_date              text,
   join_precision         text,
   role_start             text,
   role_precision         text,
   certifications         text[],          -- NULL: the source did not provide the field
   certifications_provided boolean NOT NULL,
   location               text,
   own_description        text,
   own_description_sha256 text,
   last_activity_at       date,
   fields_dropped         integer NOT NULL DEFAULT 0,
   internal_only          boolean NOT NULL DEFAULT false,
   partial                boolean NOT NULL DEFAULT false,
   hostile                boolean NOT NULL DEFAULT false,
   erased_at              timestamptz,
   erasure_reason         text,
   created_at             timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_person_profiles PRIMARY KEY (id),
   CONSTRAINT fk_person_profiles_tenant_id_account_id_accounts
      FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
   CONSTRAINT uq_person_profiles_run_url UNIQUE (tenant_id, run_id, profile_url_canonical),
   CONSTRAINT ck_person_profiles_precision CHECK (
      (join_precision IS NULL OR join_precision IN ('year', 'month', 'day'))
      AND (role_precision IS NULL OR role_precision IN ('year', 'month', 'day'))),
   CONSTRAINT ck_person_profiles_sha CHECK (own_description_sha256 IS NULL
      OR own_description_sha256 ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_person_profiles_certs CHECK (erased_at IS NOT NULL
      OR certifications_provided = (certifications IS NOT NULL)),
   CONSTRAINT ck_person_profiles_description_len CHECK (own_description IS NULL
      OR char_length(own_description) <= 4000),
   CONSTRAINT ck_person_profiles_erased CHECK (erased_at IS NULL OR (name IS NULL
      AND current_title IS NULL AND headline IS NULL AND employer IS NULL
      AND prior_employers IS NULL AND join_date IS NULL AND role_start IS NULL
      AND certifications IS NULL AND location IS NULL AND own_description IS NULL
      AND own_description_sha256 IS NULL AND last_activity_at IS NULL
      AND erasure_reason IS NOT NULL))
);
CREATE INDEX ix_person_profiles_account_id_url ON person_profiles (account_id, profile_url_canonical);
ALTER TABLE person_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE person_profiles FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON person_profiles TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON person_profiles TO app_rw;
GRANT UPDATE (name, current_title, headline, employer, prior_employers, join_date, join_precision,
   role_start, role_precision, certifications, location, own_description, own_description_sha256,
   last_activity_at, erased_at, erasure_reason) ON person_profiles TO app_rw;
GRANT USAGE ON SEQUENCE person_profiles_id_seq TO app_rw;
CREATE TRIGGER trg_person_profiles_erasure_only BEFORE UPDATE ON person_profiles
   FOR EACH ROW EXECUTE FUNCTION people_erasure_only('name', 'current_title', 'headline',
      'employer', 'prior_employers', 'join_date', 'join_precision', 'role_start', 'role_precision',
      'certifications', 'location', 'own_description', 'own_description_sha256',
      'last_activity_at');

CREATE TABLE person_posts (
   id                    bigserial NOT NULL,
   tenant_id             uuid NOT NULL DEFAULT app_tenant(),
   account_id            uuid NOT NULL,
   run_id                uuid NOT NULL,
   profile_url_canonical text NOT NULL,
   post_url              text NOT NULL,
   posted_at             date,
   excerpt               text NOT NULL,
   excerpt_sha256        text NOT NULL,
   raw_ref               text NOT NULL,
   call_ref              text NOT NULL,
   internal_only         boolean NOT NULL DEFAULT false,
   hostile               boolean NOT NULL DEFAULT false,
   erased_at             timestamptz,
   erasure_reason        text,
   created_at            timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_person_posts PRIMARY KEY (id),
   CONSTRAINT fk_person_posts_tenant_id_account_id_accounts
      FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
   CONSTRAINT uq_person_posts_run_post UNIQUE (tenant_id, run_id, profile_url_canonical, post_url),
   CONSTRAINT ck_person_posts_excerpt CHECK (char_length(excerpt) <= 2000),
   CONSTRAINT ck_person_posts_sha CHECK (excerpt_sha256 ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_person_posts_erased CHECK (erased_at IS NULL OR (excerpt = ''
      AND erasure_reason IS NOT NULL))
);
ALTER TABLE person_posts ENABLE ROW LEVEL SECURITY;
ALTER TABLE person_posts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON person_posts TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON person_posts TO app_rw;
GRANT UPDATE (excerpt, erased_at, erasure_reason) ON person_posts TO app_rw;
GRANT USAGE ON SEQUENCE person_posts_id_seq TO app_rw;
CREATE TRIGGER trg_person_posts_erasure_only BEFORE UPDATE ON person_posts
   FOR EACH ROW EXECUTE FUNCTION people_erasure_only('excerpt');

-- One row per posts pull (also when it returned no post), so a resume never pulls twice.
CREATE TABLE person_post_pulls (
   id                    bigserial NOT NULL,
   tenant_id             uuid NOT NULL DEFAULT app_tenant(),
   account_id            uuid NOT NULL,
   run_id                uuid NOT NULL,
   profile_url_canonical text NOT NULL,
   post_count            integer NOT NULL,
   created_at            timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_person_post_pulls PRIMARY KEY (id),
   CONSTRAINT fk_person_post_pulls_tenant_id_account_id_accounts
      FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
   CONSTRAINT uq_person_post_pulls_run_url UNIQUE (tenant_id, run_id, profile_url_canonical),
   CONSTRAINT ck_person_post_pulls_count CHECK (post_count >= 0)
);
ALTER TABLE person_post_pulls ENABLE ROW LEVEL SECURITY;
ALTER TABLE person_post_pulls FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON person_post_pulls TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON person_post_pulls TO app_rw;
GRANT USAGE ON SEQUENCE person_post_pulls_id_seq TO app_rw;

CREATE TABLE person_events (
   id          bigserial NOT NULL,
   tenant_id   uuid NOT NULL DEFAULT app_tenant(),
   account_id  uuid NOT NULL,
   run_id      uuid NOT NULL,
   person_key  text NOT NULL,
   kind        text NOT NULL,
   into_key    text,
   rule_id     text NOT NULL,
   actor       text NOT NULL,
   detail      text,
   created_at  timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_person_events PRIMARY KEY (id),
   CONSTRAINT fk_person_events_tenant_id_account_id_accounts
      FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
   CONSTRAINT ck_person_events_kind CHECK (kind IN ('merge', 'unmerge', 'scope', 'erase')),
   CONSTRAINT ck_person_events_actor CHECK (actor IN ('pipeline', 'operator')),
   CONSTRAINT ck_person_events_merge CHECK ((kind = 'merge') = (into_key IS NOT NULL))
);
CREATE INDEX ix_person_events_run_id_person_key ON person_events (run_id, person_key);
ALTER TABLE person_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE person_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON person_events TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON person_events TO app_rw;
GRANT USAGE ON SEQUENCE person_events_id_seq TO app_rw;

CREATE TABLE people_pipeline_runs (
   run_id      uuid NOT NULL,
   tenant_id   uuid NOT NULL DEFAULT app_tenant(),
   account_id  uuid NOT NULL,
   as_of       date NOT NULL,
   config_hash text NOT NULL,
   record_json jsonb NOT NULL,     -- counts, exits, evidence, codes; no name, title or text
   claims_ref  text,               -- templated claims and Findings in the payload store
   created_at  timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_people_pipeline_runs PRIMARY KEY (run_id),
   CONSTRAINT fk_people_pipeline_runs_tenant_id_account_id_accounts
      FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
   CONSTRAINT ck_people_pipeline_runs_hash CHECK (config_hash ~ '^[0-9a-f]{64}$')
);
ALTER TABLE people_pipeline_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE people_pipeline_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON people_pipeline_runs TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON people_pipeline_runs TO app_rw;
