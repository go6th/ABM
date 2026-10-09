-- Remove revision 0038 unit-cost tables in dependency-safe order.
DROP TABLE IF EXISTS run_anomaly_events CASCADE;
DROP TABLE IF EXISTS unit_cost_rollups CASCADE;
