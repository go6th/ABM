-- Effort beside each model choice (owner decision 2026-10-20): a user picks medium or high
-- effort for the research role and the analysis role, next to the model. NULL: not chosen (the
-- catalogue's default effort applies). Additive: two nullable columns with a CHECK on the known
-- effort levels (the offered set lives in config/models.yaml `choices.efforts`).

ALTER TABLE user_preferences ADD COLUMN research_effort text;
ALTER TABLE user_preferences ADD COLUMN analysis_effort text;
ALTER TABLE user_preferences ADD CONSTRAINT ck_user_preferences_effort CHECK (
   (research_effort IS NULL OR research_effort IN ('low', 'medium', 'high', 'xhigh', 'max'))
   AND (analysis_effort IS NULL OR analysis_effort IN ('low', 'medium', 'high', 'xhigh', 'max')));
