# ADR-0046: the telemetry contract and one instrumentation library (D-56)

- Status: Proposed (D-56)
- Date: 2026-10-06
- Deciders: TODO

## Context
Observability work package (docs/observability/README.md). Observability must not leak tenant
data, change run behaviour, or become a second source of truth for money or run status.

## Decision
Metrics, spans and log fields are declared in `config/telemetry/{metrics,spans,logs}.yaml`. Every
metric names its type, unit, labels (closed values or a bound), a series bound at least the product
of its label cardinalities, and an owner. Tenant, account, run, person, user, domain, URL, email,
name and free-text labels are forbidden. `src/obs` is the only place that acquires tracers, meters
and loggers (`scripts/check_telemetry.py`). Every sink goes through `obs.scrub` (allow-list plus
shape filter: URLs with queries or credentials, emails, token shapes, control characters, planted
canaries). Export is bounded and non-blocking (`obs.exporters.BoundedExporter`); a telemetry
failure is a counted drop and never reaches the caller (O1). Backend: Cloud Monitoring, Trace and
Logging via OpenTelemetry; the OTLP or Cloud Trace exporter is not installed here (V-OBS-1).

## Consequences
The run id is the trace id (the UUID's 128 bits); span ids are deterministic from (run, stage,
attempt, unit key), so a resumed slice re-emits the same ids. The catalog hash is a resource
attribute. Cost: a contract change edits YAML and passes the check.
