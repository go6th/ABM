# ADR-0109: each process registers only the routes of its SERVICE_ROLE

- Status: Accepted
- Date: 2026-11-03
- Deciders: owner (fix prompt B, task 1)

## Context

The api and worker Cloud Run services run one FastAPI application. Every process registered every
router, so the worker served `/v1` and `/operator` (sign-in included) and the api served `/tasks`;
only Cloud Run ingress and IAM kept them apart, and nothing in the repository could test that.

## Decision

`account_intelligence.api.planes` maps each role to planes: `api` serves `/v1/*` and
`/operator/*`, `worker` serves `/tasks/*`, `job` and `migrate` only the probes, and `all` (the
default, for dev and test) everything. `create_app` registers only the routers of its role, so an
unserved route answers 404 like any unknown path and the catalogue's default deny is untouched.
A staging or production web process with `SERVICE_ROLE` unset or `all` refuses to start.
`scripts/check_route_catalog.py` gives every route one plane and checks that the api app serves
no `/tasks` route, the worker app no `/v1` or `/operator` route, and that the two together equal
the dev app. A cloud api with no task queue never runs a research job in-process
(`replica/jobs.run_or_enqueue`): the job stays queued for `runs.recovery`. Terraform already
sets `SERVICE_ROLE` on every service and job, so no infrastructure change is needed.

## Consequences

A misconfigured worker (the image default is `job`) serves only probes and fails closed instead of
exposing sign-in routes. Tests: `tests/api/test_service_role_planes.py`,
`tests/unit/test_cloud_never_runs_jobs_in_process.py`.
