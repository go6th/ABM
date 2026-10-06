-- Rollback of 0018 (only before the tables hold records: call records, findings and eval results
-- are history and are never dropped once used).
DROP TABLE IF EXISTS eval_baselines;
DROP TABLE IF EXISTS eval_metrics;
DROP TABLE IF EXISTS eval_case_results;
DROP TABLE IF EXISTS eval_runs;
DROP TABLE IF EXISTS verifier_findings;
DROP FUNCTION IF EXISTS llm_health_record(text, text, timestamptz, integer, double precision,
   double precision);
DROP TABLE IF EXISTS llm_candidate_health;
DROP TABLE IF EXISTS llm_call_attempts;
DROP TABLE IF EXISTS llm_calls;
