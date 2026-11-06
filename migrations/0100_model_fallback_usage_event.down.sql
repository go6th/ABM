-- Reverse 0100: no detail column, the five usage event kinds of 0025. The constraint is NOT VALID
-- so any recorded 'model_fallback' rows (billing records) stay; new ones are refused.
ALTER TABLE usage_events
   DROP CONSTRAINT IF EXISTS ck_usage_events_detail,
   DROP COLUMN IF EXISTS detail,
   DROP CONSTRAINT ck_usage_events_kind,
   ADD CONSTRAINT ck_usage_events_kind CHECK (kind IN ('account_added', 'run_started',
      'run_settled', 'refresh_run', 'export_created')) NOT VALID;
