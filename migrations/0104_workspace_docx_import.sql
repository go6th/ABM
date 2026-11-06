-- Migration 0104 (2026-11-03, workspace import phase 3): what the Word deliverables of an
-- uploaded workspace carry that the native tables had nowhere to keep.
--
-- sources: how_retrieved (the register's "How retrieved": page read, feed item, search
--   result...), what_it_supports (the register's "What it supports", authored), aliases (the
--   codes the uploaded files used for this source, S12 in a workspace's own numbering, so a
--   citation in imported prose resolves to the registry's code).
-- report_sections: the kinds an Account Summary writes that the research graph never stored as
--   their own kind: how_wrong ("How this could be wrong"), sharpen ("The simplest way to
--   sharpen it"), baseline ("Company and transformation baseline"), caution ("Cautions for
--   sensitive findings"), interpretation (the people section's reading).
-- coverage_rows: needs and what_remains, the tracker's authored columns (label maps at export
--   before).
ALTER TABLE sources
   ADD COLUMN how_retrieved text,
   ADD COLUMN what_it_supports text,
   ADD COLUMN aliases text[] NOT NULL DEFAULT '{}';
ALTER TABLE report_sections DROP CONSTRAINT ck_report_sections_kind;
ALTER TABLE report_sections ADD CONSTRAINT ck_report_sections_kind CHECK (kind IN ('narrative',
   'caveat', 'executive_voice', 'hypothesis', 'first_call_question', 'decision_change',
   'stands_out', 'lane_meaning', 'how_wrong', 'sharpen', 'baseline', 'caution', 'interpretation'));
ALTER TABLE coverage_rows
   ADD COLUMN needs text,
   ADD COLUMN what_remains text;
