-- Migration 0098 (2026-11-03): bound the scheduled-refresh tick (fix prompt A, task 4; ADR-0108).
--
-- POST /tasks/schedules-tick started every due account in one go (replica/schedules.tick). With
-- the research caps off a large batch of due accounts started together. These two platform
-- defaults bound one tick:
--   schedule_tick_max_starts      starts of one tick, scheduled refreshes and promoted waiting
--                                 jobs together (default 10)
--   schedule_tick_max_per_tenant  starts of one tick for one client (default 3)
-- An account over the ceiling keeps its next_refresh_at and is due again on the next tick (every
-- 15 minutes); a waiting job over the ceiling stays waiting.
-- Rollback: 0098_schedule_tick_bounds.down.sql (drops the two columns; nothing else changes).

ALTER TABLE platform_defaults
   ADD COLUMN schedule_tick_max_starts integer NOT NULL DEFAULT 10,
   ADD COLUMN schedule_tick_max_per_tenant integer NOT NULL DEFAULT 3,
   ADD CONSTRAINT ck_platform_defaults_tick_starts CHECK (schedule_tick_max_starts BETWEEN 1 AND 1000),
   ADD CONSTRAINT ck_platform_defaults_tick_per_tenant
      CHECK (schedule_tick_max_per_tenant BETWEEN 1 AND schedule_tick_max_starts);
