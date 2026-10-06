-- Rollback of 0019 (derived and observation tables only).
DROP TABLE IF EXISTS canary_probe_results;
DROP TABLE IF EXISTS stuck_run_observations;
DROP TABLE IF EXISTS slo_snapshots;
DROP TABLE IF EXISTS ops_rollup_watermarks;
DROP TABLE IF EXISTS ops_cost_rollups;
