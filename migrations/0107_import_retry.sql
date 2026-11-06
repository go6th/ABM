-- Migration 0107 (2026-11-04, ingestion hardening PR3): bounded retry of an import, per part.
--
-- A part that failed was retried by nobody (the document was marked imported) and an import that
-- failed as a whole was retried by the sweep on every pass, without end. Now a failed part gets at
-- most three attempts with a growing wait (next_attempt_at: 5 minutes after the first, 10 after
-- the second; none after the third, which is final), and the sweep retries only those parts, never
-- the parts that succeeded nor an upload whose import succeeded (documents.deliverables).
ALTER TABLE import_run_parts ADD COLUMN next_attempt_at timestamptz;
CREATE INDEX ix_import_run_parts_retry ON import_run_parts (tenant_id, next_attempt_at)
   WHERE status IN ('failed', 'running', 'pending');
