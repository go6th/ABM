# Operations guide

This is the operator entry point for the current codebase. It describes implemented local and
Google Cloud checks; production service ownership, alert routing, and SLO evidence still require
owner configuration. Detailed procedures are in [runbooks/](runbooks/).

## Health and readiness

- `GET /healthz` is process liveness.
- `GET /readyz` is dependency readiness and must return 200 before a revision receives traffic.
- A not-ready response is a stop signal. Inspect its named checks, database connectivity, migration
  revision, task scheduler, and configured adapters before restarting or routing traffic.
- The current Compose file does not start the API. Run the API separately with `make run` after
  `make up` and `make migrate`; Compose readiness is therefore not an end-to-end product check.

## Run operations

- Run lifecycle and settlement rules are owned by `src/account_intelligence/runs/lifecycle.py`.
- Do not edit run, wallet ledger, tracker, or audit rows by hand.
- For stuck slices, duplicate task delivery, checkpoint recovery, approval expiry, provider outage,
  and budget breaches, use the corresponding procedures in `runbooks/`.
- Provider and model adapters that raise `NotConfigured` are expected boundaries until registrations
  and credentials are provided. Local checks must use synthetic fixtures and stubs only.

## Incidents and escalation

The repository has alert-specific runbooks under `docs/runbooks/`, including database, isolation,
provider, scheduler, stuck-run, budget, and telemetry incidents. Alert-to-runbook references can be
checked with `uv run --frozen python scripts/check_runbooks.py`. Production paging destinations,
primary/secondary on-call names, and escalation times are not configured here; the service owner
must supply them before launch.

## Backup, restore, and disaster recovery

Use [restore.md](runbooks/restore.md) for the reviewed database restore procedure and
[regional-failover.md](runbooks/regional-failover.md) for the regional rehearsal. The repository's
local pre-finalization ZIP is a source-tree recovery point, not a database backup. No production
Cloud SQL backup, PITR, restore-time, or regional failover claim is made until a cloud game day is
recorded.

## Release and change control

Check [RELEASE_REPORT.md](RELEASE_REPORT.md) before every release. A local green test subset does
not establish production readiness. Keep the release image immutable by digest, run reviewed
migrations before rollout, require the protected GitHub environment approval, and record the
previous revision and migration version for rollback planning.
