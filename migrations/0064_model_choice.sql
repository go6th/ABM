-- Model choice (owner decision 2026-10-14, Phase 4B section 1). Model ids stay in config
-- (config/models.yaml `choices`); NULL here means "the catalogue's default".
--
-- platform_defaults: which models are enabled, and the default research and analysis models
-- (they replace the old "Model split"). client_settings: the models a client may use (NULL: the
-- enabled models except those that need the client's permission, Kimi K3). user_preferences: a
-- user's two choices, private to the user. account_runs.models and chat_threads.analysis_model
-- record what a run or a conversation used.

ALTER TABLE platform_defaults
   ADD COLUMN enabled_models text[],
   ADD COLUMN default_research_model text,
   ADD COLUMN default_analysis_model text,
   DROP COLUMN model_split;

ALTER TABLE client_settings ADD COLUMN allowed_models text[];

ALTER TABLE account_runs ADD COLUMN models jsonb;
ALTER TABLE research_jobs ADD COLUMN models jsonb;
ALTER TABLE chat_threads ADD COLUMN analysis_model text;

CREATE TABLE user_preferences (
   tenant_id       uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   owner_user_id   text NOT NULL DEFAULT app_user(),
   research_model  text,
   analysis_model  text,
   updated_at      timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_user_preferences PRIMARY KEY (tenant_id, owner_user_id),
   CONSTRAINT ck_user_preferences_ids CHECK (
      (research_model IS NULL OR research_model ~ '^[a-z0-9][a-z0-9.-]{1,62}$')
      AND (analysis_model IS NULL OR analysis_model ~ '^[a-z0-9][a-z0-9.-]{1,62}$'))
);
ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_preferences FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_owner_isolation ON user_preferences TO app_rw
   USING (tenant_id = (SELECT app_tenant())
          AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'))
   WITH CHECK (tenant_id = (SELECT app_tenant())
          AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'));
GRANT SELECT, INSERT, UPDATE ON user_preferences TO app_rw;
