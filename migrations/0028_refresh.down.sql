-- Rollback of 0028_refresh.sql (development only; production never contracts in the same release).
DROP TABLE IF EXISTS missing_observations;
DROP TABLE IF EXISTS delta_items;
DROP TABLE IF EXISTS deltas;
DROP TABLE IF EXISTS signal_history;
DROP TABLE IF EXISTS refresh_row_plans;
DROP TABLE IF EXISTS refresh_runs;
DROP TABLE IF EXISTS refresh_slots;
DROP TABLE IF EXISTS shelf_life_overrides;
