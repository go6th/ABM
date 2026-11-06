# ADR-0135: Workflow v1 is deprecated; abm_agents counts as on when it has no rule

- Status: Accepted
- Date: 2026-11-03
- Deciders: owner (fix prompt F, task 3)

## Context

Two graphs can run a `full` company research. `agents.production.wiring.uses_abm` picks:

* the ABM agent graph always for `pull` and `build` runs, for a run started by the research job
  engine (`run_input.job_id`) and for the public-sources run linked to a file analysis
  (`run_input.linked_file_run`);
* for any other `full` run, the ABM graph only when the tenant's `abm_agents` feature flag is on.
  Flags are deny by default (`admin/flags.evaluate` returns False for a missing rule), so every
  tenant without a rule got the 13-stage Workflow v1 (`account_intelligence/workflow/`,
  `src/parallel/`, `verify/stage.py`, the planner/extractor/lead-query/source-reread prompts).

Evidence that the ABM graph is already the product path for every tenant:

* the web starts a company research through the research job engine (always ABM); its only
  `POST /v1/runs` call is the file analysis (`web/components/accounts/NewResearch.tsx`, body
  `input_mode: "file"`), which runs `documents.graph.FileGraphFactory`, not v1;
* no seed writes an `abm_agents` rule (`scripts/seed_replica_demo.py`, `scripts/seed_pilot.py`,
  `config/`): in the hermetic seed every tenant had the flag off, so only a direct API caller of
  `POST /v1/runs` with `input_mode: company` reached v1.

## Decision

1. `abm_agents` alone among the flags treats a missing rule as on: `uses_abm` calls
   `abm_flag_on(rule, tenant, plan)`, which returns True when no rule was ever written and
   otherwise defers to `admin.flags.evaluate`. This is the least surprising mechanism: no
   migration, no seed row, every other flag stays deny by default, and the existing operator path
   still controls it. An operator who must send a client's `full` runs back to v1 writes an
   explicit rule that does not list that client (`POST /operator/v1/flags`, e.g. `{}` for all).
2. When Workflow v1 runs, the runner logs `workflow_v1.deprecated` (WARNING, once per run at slice
   0, fields `graph_version`, `reason`), catalogued in `config/telemetry/logs.yaml`.
3. Removal: Workflow v1 is deleted (parked) in the **release after next**. Until then it stays
   whole and its tests stay green; tests that pin v1 behaviour opt in with the
   `pin_workflow_v1` fixture (`tests/system/conftest.py`).

## Consequences

* A tenant with no rule now runs the ABM graph for `POST /v1/runs` `full` company runs. A tenant
  that already has an explicit `abm_agents` rule keeps whatever that rule says: on staging, check
  `SELECT * FROM feature_flags WHERE flag = 'abm_agents' ORDER BY version DESC LIMIT 1` before the
  release. If any rule exists, the default change does nothing until the rule is replaced.
* Before removal: confirm the `workflow_v1.deprecated` log has been silent on staging for one
  release, then park `account_intelligence/workflow/stages`, `src/parallel`, `verify/stage.py`,
  `config/workflow_v1.yaml` and the v1 prompts, and the `pin_workflow_v1` tests, in one change.
* `tests/unit/test_abm_flag_default.py` pins the default and the explicit-rule override.
