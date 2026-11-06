-- Undo migration 0104: the Word import columns and section kinds.
ALTER TABLE coverage_rows
   DROP COLUMN IF EXISTS needs,
   DROP COLUMN IF EXISTS what_remains;
ALTER TABLE report_sections DROP CONSTRAINT ck_report_sections_kind;
ALTER TABLE report_sections ADD CONSTRAINT ck_report_sections_kind CHECK (kind IN ('narrative',
   'caveat', 'executive_voice', 'hypothesis', 'first_call_question', 'decision_change',
   'stands_out', 'lane_meaning'));
ALTER TABLE sources
   DROP COLUMN IF EXISTS how_retrieved,
   DROP COLUMN IF EXISTS what_it_supports,
   DROP COLUMN IF EXISTS aliases;
