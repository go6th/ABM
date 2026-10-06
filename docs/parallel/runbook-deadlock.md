# Runbook: SchedulerDeadlock

**Symptom.** A run's research phase raised `SchedulerDeadlock`; the remaining units are `blocked`
with `dependency_failed` and their rows ended Not done with that reason.

**What it means.** Nothing was ready, nothing was in flight and work was pending: a unit depends on
a key that is not in the plan or can never settle. The loop never hangs; it blocks the remainder.

**First checks.**
```sql
SELECT unit_key, depends_on, plan_hash FROM work_units WHERE run_id = :run ORDER BY unit_key;
SELECT unit_key, event, reason, at FROM work_unit_events WHERE run_id = :run ORDER BY id;
```
Compare `depends_on` with the plan's keys; `config/parallel.yaml` is validated at startup, so a
missing key points at a plan built from a different config version (`plan_hash`) or a defect.

**Mitigation.** Fix the config or the planner and start a new run (a terminal run is never
reopened). Record the defect with a test that reproduces the plan.
