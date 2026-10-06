# Observability

Observes and derives; never repairs a run, never holds money truth, never changes behaviour (O1).

## Telemetry contract (ADR-0046)
`config/telemetry/{metrics,spans,logs}.yaml`, validated by `obs.catalog` at load and by
`scripts/check_telemetry.py` in CI: undeclared metrics or span names, forbidden labels, series
bounds, and tracer, meter or logger acquisition outside `src/obs` and `obs.py` edge modules fail.

## Tracing model
Trace id = run UUID (128 bits); span ids deterministic from (run, name, key), so a resumed slice
re-emits the same ids; the root span `run` is emitted once after the settling transaction commits,
start = `created_at`, end = settlement. Attributes pass the span allow-list and the shape filter.
Not yet wired: stage, unit and call spans in the engine, executor and connectors, and `traceparent`
propagation through the scheduler payload (see STATUS).

## SLOs and error budget (ADR-0048)
`config/telemetry/slos.yaml`; arithmetic in `obs.slo` (exact fractions, NoData without traffic).
When a budget is exhausted, releases stop except fixes (proposal, D-58).

## Cost derivation (ADR-0047)
`obs.costs`: ledger to `ops_cost_rollups` (recomputable), reconciled exactly; drift and unknown
cost alert. Operator read: `GET /operator/v1/ops/cost/{run_id}`.

## Alerts and dashboards (ADR-0049)
`config/telemetry/{alerts,dashboards,severity}.yaml` to `infra/modules/observability/generated_*.tf.json`
via `scripts/gen_observability.py` (`--check` in CI). Never applied by the build.

## Retention and access
Log retention is a Terraform variable (TODO: product owner). Operator endpoints
(`/operator/v1/runs/{id}/timeline`, `/operator/v1/ops/*`) fail closed until the operator plane
exists (D-33); they return identifiers, counts and amounts only.

## Disaster recovery step 7 (re-point observability)
Set `project_id` and the channel variables of `infra/modules/observability` for the recovery
project, plan, apply (owner), then verify with `docs/runbooks/queries/recent_activity.sql` and a
heartbeat in the new project.
