-- Undo migration 0084: the section 1 kinds. Rows of the two kinds are removed first, or the
-- narrower kind check could not be restored.
ALTER TABLE report_sections DROP CONSTRAINT IF EXISTS ck_report_sections_lane;
DELETE FROM report_sections WHERE kind IN ('stands_out', 'lane_meaning');
ALTER TABLE report_sections DROP COLUMN IF EXISTS lane;
ALTER TABLE report_sections DROP CONSTRAINT ck_report_sections_kind;
ALTER TABLE report_sections ADD CONSTRAINT ck_report_sections_kind CHECK (kind IN ('narrative',
   'caveat', 'executive_voice', 'hypothesis', 'first_call_question', 'decision_change'));
