# ADR-0123: the runtime database role is split by service; leaving the tenant scope is allow-listed

- Status: Accepted (code and migration); the Terraform part is planned, not applied
- Date: 2026-11-03
- Deciders: owner (fix prompt D, task 2)

## Context

One role, `app_rw`, served the api, the worker and the jobs: the api process could write the
provider-call ledger (`tool_calls`), take run leases and write LangGraph checkpoints, none of which
it does. The Cloud SQL IAM users of all three services were members of `app_rw` (migration 0071).
In code, `untenanted_session()` and the system scope (`set_system`) lived in `db/session.py`,
which every module imports, so nothing but a test over `api/` kept new callers out.

## Decision

**Roles (migration 0101).** Three NOLOGIN, NOBYPASSRLS group roles; the IAM users join them:

| Role | Rights | Who |
|---|---|---|
| `app_api` | app_rw's grants (tables, columns, sequences, functions) **minus** every write on `tool_calls`, every right on `run_leases`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`, every write on `checkpoints`, and EXECUTE on the five sweeper discovery lookups; a copy (`<policy>_api`) of every app_rw policy | api IAM user |
| `app_worker` | member of app_rw (INHERIT, no SET) | worker and job IAM users |
| `app_sweeper` | EXECUTE on `runs_orphaned`, `runs_stalled`, `documents_orphaned`, `document_parts_orphaned`, `research_files_orphaned`, `operator_tenant_overview`; nothing else | nobody yet |

`app_rw` itself is unchanged: the local stack, the tests and any IAM user not yet moved keep
working. Its policies are untouched (check 7 of `check_rls_catalog.sql` wants an app_rw-only
policy per tenant table), so app_api gets copies with the same command, kind and predicates.
`service_roles_sync()` derives app_api's grants and copies from app_rw's catalog entries (the
exclusions are the only hand-written list) and moves the IAM users named in
`APP_API_IAM_USERS` / `APP_WORKER_IAM_USERS` (migrate job) from app_rw to their role.
`migrations/env.py` calls it after **every** migration run, so a later migration that grants
app_rw or adds an app_rw policy reaches app_api without a hand-written copy;
`tests/isolation/test_service_roles.py` fails if they drift.

**Code mapping.** `platform/gcp/adapters.py`: `DB_ROLES` maps `SERVICE_ROLE` api -> app_api,
worker and job -> app_worker, migrate -> none, all (dev) -> app_rw. At startup in the cloud
(`db.session.bind_cloud_engine`, `worker/job.py`) the roles granted directly to the IAM user are
checked: another service's role or no runtime role fails the start; still in app_rw logs
`db.role_pending` and runs (the rollout window before the migrate job moved the user).

**Where the code disagreed with the prompt.** The api *reads* `checkpoints` (the run steps panel,
`api/routes/runs.py`, and the live page, `replica/live.py`, read `checkpoint -> 'versions_seen'`)
and reads `tool_calls` (run counters, stop). So app_api keeps SELECT on those two; "no access to
checkpoint tables" holds for the blob, write and migration tables only. The sweeper is not a
separate process: it runs inside the worker and, after discovery, ends runs, settles jobs and
resumes research in tenant transactions, so it must run as app_worker. app_sweeper is therefore
created with exactly the discovery rights but no service signs in as it until the sweeper becomes
its own job. SERVICE_ROLE has no `sweeper` value.

**The way out of the tenant scope.** `untenanted_session()`, `set_system()` and `system_scope()`
moved to `account_intelligence/db/system_scope.py` (`tenant_txn` stays in `session.py`; the
session module no longer exports them, `tests/guardrails/test_untenanted_sessions.py` checks).
An import-linter `forbidden` contract lets only 17 modules import it: api `auth`, `main`,
`operator`, `team_invites`, `tenant_plane`, routes `admin_console`, `auth`, `feedback`, `library`,
`operator_admin`, `sso`, `tasks`; `replica.auto_resume`, `replica.schedules`;
`worker.__main__`; `documents.deliverables`, `documents.service`. (`operator_sessions.py` and
`flags.py`, named in the brief, import neither helper.) `tenant_txn(..., system=True)` is still
open to every module: it stays inside one tenant, so it is not the same power, but it is the next
thing to narrow.

**replica and documents.** A second contract forbids `account_intelligence.replica` and
`documents` from importing `account_intelligence.api` or `account_intelligence.app` (indirect
imports included). One violation exists today and is ratcheted:
`replica.isolation -> api.policy` (the isolation probe reads the route catalogue).

**Terraform (plan only).** The migrate job gets `APP_API_IAM_USERS` (api IAM user) and
`APP_WORKER_IAM_USERS` (worker and job IAM users). Plan: the migrate job is updated in place;
nothing else of this task.

## Consequences

Until the owner applies Terraform and runs the migrate job, the IAM users stay in app_rw and the
services log `db.role_pending`. After it, the api cannot write the ledger, leases or checkpoint
internals even with a SQL injection. The api no longer receives `CHECKPOINT_DATABASE_URL`
(ADR-0125): before that change the api held a password URL to the database, which would have
undercut this split. Check in the console after the migrate job: `SELECT r.rolname, m.rolname
FROM pg_auth_members a JOIN pg_roles r ON r.oid = a.roleid JOIN pg_roles m ON m.oid = a.member
WHERE m.rolname LIKE 'ai-staging-%'` shows app_api / app_worker, not app_rw.
