-- The Account Summary's section 1 in the reference shape (2026-10-26): distill now writes what
-- stands out (kind stands_out) and what the facts likely mean for each lane (kind lane_meaning,
-- interpretation labeled), beside the account picture (narrative), how this could be wrong
-- (caveat) and the simplest way to sharpen it (decision_change). A lane meaning names its lane
-- id (config/lanes.yaml); no other kind carries one. Additive: one nullable column, the kind
-- vocabulary widened.

ALTER TABLE report_sections DROP CONSTRAINT ck_report_sections_kind;
ALTER TABLE report_sections ADD CONSTRAINT ck_report_sections_kind CHECK (kind IN ('narrative',
   'caveat', 'executive_voice', 'hypothesis', 'first_call_question', 'decision_change',
   'stands_out', 'lane_meaning'));
ALTER TABLE report_sections ADD COLUMN lane text;
ALTER TABLE report_sections ADD CONSTRAINT ck_report_sections_lane CHECK (
   (kind = 'lane_meaning' AND lane ~ '^[a-z0-9_]{1,64}$')
   OR (kind <> 'lane_meaning' AND lane IS NULL));
