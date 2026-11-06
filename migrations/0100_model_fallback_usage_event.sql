-- Migration 0100 (2026-11-03): a model fallback is a usage event (fix prompt C, task 4; ADR-0119).
--
-- A research step whose chosen model (Kimi K3 or Grok 4.5) could not meet its output schema is
-- served by the role's Claude default (agents.production.wiring.RunDepsModel). That is a billing
-- event: the bill differs from an estimate made for the chosen model. One usage_events row of
-- kind 'model_fallback' per fallen-back request (idempotent on its key), units 1, and a detail
-- object with both model ids, why, what the discarded attempts cost and what the Claude calls
-- added (decimal strings, USD). `detail` is optional for every other kind (they keep NULL).
-- Rollback: 0100_model_fallback_usage_event.down.sql (drops `detail`; the kind constraint goes
-- back to the five kinds of 0025 as NOT VALID, so recorded fallback events, a billing record,
-- are kept).

ALTER TABLE usage_events
   DROP CONSTRAINT ck_usage_events_kind,
   ADD CONSTRAINT ck_usage_events_kind CHECK (kind IN ('account_added', 'run_started',
      'run_settled', 'refresh_run', 'export_created', 'model_fallback')),
   ADD COLUMN detail jsonb,
   ADD CONSTRAINT ck_usage_events_detail CHECK (detail IS NULL OR jsonb_typeof(detail) = 'object');
