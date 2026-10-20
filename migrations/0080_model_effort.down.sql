-- Undo migration 0080: the effort choices.
ALTER TABLE user_preferences DROP CONSTRAINT IF EXISTS ck_user_preferences_effort;
ALTER TABLE user_preferences DROP COLUMN IF EXISTS analysis_effort;
ALTER TABLE user_preferences DROP COLUMN IF EXISTS research_effort;
