-- Rollback of 0016 (contract direction; only for a failed deploy before any file was delivered:
-- deliverables and report_sections rows are client records and are never dropped once used).
DROP TABLE IF EXISTS deliverables;
DROP TABLE IF EXISTS report_sections;
ALTER TABLE facts DROP CONSTRAINT IF EXISTS ck_facts_lane_shape;
ALTER TABLE facts DROP COLUMN IF EXISTS lane;
