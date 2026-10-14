-- Reverse the model choice.
DROP TABLE IF EXISTS user_preferences;
ALTER TABLE chat_threads DROP COLUMN IF EXISTS analysis_model;
ALTER TABLE account_runs DROP COLUMN IF EXISTS models;
ALTER TABLE research_jobs DROP COLUMN IF EXISTS models;
ALTER TABLE client_settings DROP COLUMN IF EXISTS allowed_models;
ALTER TABLE platform_defaults
   DROP COLUMN IF EXISTS enabled_models,
   DROP COLUMN IF EXISTS default_research_model,
   DROP COLUMN IF EXISTS default_analysis_model,
   ADD COLUMN IF NOT EXISTS model_split text NOT NULL DEFAULT 'split'
      CONSTRAINT ck_platform_defaults_split CHECK (model_split IN ('split', 'single'));
