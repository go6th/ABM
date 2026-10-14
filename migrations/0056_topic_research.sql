-- Research on a topic the user types ("their Salesforce Data Cloud rollout"): a refresh scope of
-- its own, carrying the topic text (the user's own words, bounded; it is handed to the research
-- engine as the question to research, never shown to another user or tenant).
ALTER TABLE research_jobs ADD COLUMN topic text;
ALTER TABLE research_jobs DROP CONSTRAINT ck_research_jobs_scope;
ALTER TABLE research_jobs ADD CONSTRAINT ck_research_jobs_scope
   CHECK (scope IN ('jobs', 'people', 'full', 'file', 'topic'));
ALTER TABLE research_jobs ADD CONSTRAINT ck_research_jobs_topic
   CHECK ((scope = 'topic') = (topic IS NOT NULL)
          AND (topic IS NULL OR char_length(btrim(topic)) BETWEEN 3 AND 300));
