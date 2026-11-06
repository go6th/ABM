# 06 Data and tenant isolation

Verified against the code on 2026-11-03.

See also: [README](README.md) · [01 Architecture and planes](01-architecture-and-planes.md) · [02 Request path and auth](02-request-path-and-auth.md) · [03 Research run lifecycle](03-research-run-lifecycle.md) · [04 Model routing](04-model-routing.md) · [05 Spend and caps](05-spend-and-caps.md) · [07 Documents and sandbox](07-documents-and-sandbox.md) · [08 Exports and gate](08-exports-and-gate.md) · [09 Ops](09-ops.md) · [10 Known gaps](10-known-gaps.md)

## 1. Row-level security: how a tenant is pinned

- Every tenant table has `ENABLE` and `FORCE ROW LEVEL SECURITY` (force applies even to the table owner) and a policy `TO app_rw` comparing `tenant_id` with `app_tenant()` (`migrations/0002_rls.sql:36-40`, `migrations/0002_rls.sql:44-56`).
- `app_tenant()` reads the transaction setting `app.tenant_id`; unset or empty gives NULL, so every policy matches zero rows (fail closed) (`migrations/0002_rls.sql:18-21`).
- `public_reader` is a NOLOGIN role used only for public rendering; it is switched to with `SET LOCAL ROLE` and its `facts` policy shows public rows only (`migrations/0002_rls.sql:13-17`, `migrations/0002_rls.sql:68-71`).
- All tenant queries go through `tenant_txn()` (`src/account_intelligence/db/session.py:204`). It sets `app.tenant_id`, `app.user_id` and `app.scope` with `set_config(..., true)`, which is transaction-local, so a pooled connection cannot carry tenant A into tenant B (`src/account_intelligence/db/session.py:214-216`, `src/account_intelligence/db/session.py:245-251`).
- Private workspaces (migration 0063): rows of "owned" tables are visible only to their own user or to a system job. Which tables are owned vs tenant-wide is declared in `config/ownership.yaml`; `tests/isolation/test_ownership.py` fails on a table in neither list (`config/ownership.yaml`).
- With no actor and no system scope, owned rows are invisible and inserts fail (`src/account_intelligence/db/session.py:13`).
- `tenant_txn(..., snapshot=True)` gives a `REPEATABLE READ, READ ONLY` transaction, used by the export builders (`src/account_intelligence/db/session.py:218-226`).

## 2. Leaving the tenant scope: `db/system_scope.py` and its allow-list

- Two escape hatches live in `src/account_intelligence/db/system_scope.py`: `untenanted_session()` (no tenant context; only definer-rights lookups return anything) and `set_system()` / `system_scope()` (a system job that sees every user's rows of one tenant) (`src/account_intelligence/db/system_scope.py:1-12`, `src/account_intelligence/db/system_scope.py:26-45`).
- The allow-list is an import-linter `forbidden` contract named "only the allow-listed modules leave the tenant scope (db.system_scope)" in `pyproject.toml`. Its `ignore_imports` lists the 17 modules that may import it: `api.auth`, `api.main`, `api.operator`, `api.team_invites`, `api.tenant_plane`, routes `admin_console`, `auth`, `feedback`, `library`, `operator_admin`, `sso`, `tasks`; `replica.auto_resume`, `replica.schedules`; `worker.__main__`; `documents.deliverables`, `documents.service` (`pyproject.toml`).
- A second contract forbids `account_intelligence.replica` and `documents` from importing the api or app packages, with one ratcheted exception (`replica.isolation -> api.policy`) (`pyproject.toml`).
- `tenant_txn(..., system=True)` is still callable from any module; it stays within one tenant (`src/account_intelligence/db/session.py:204-210`).

## 3. Runtime database roles (migration 0101, ADR-0123)

| Role | Rights | Who signs in as it |
|---|---|---|
| `app_rw` | original runtime role, unchanged; LOGIN, NOBYPASSRLS (`migrations/0002_rls.sql:11`) | local stack, tests, any IAM user not yet moved |
| `app_api` | app_rw's grants minus writes on `tool_calls` and `checkpoints`, minus all rights on `run_leases`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`, minus EXECUTE on the sweeper discovery lookups; its own `<policy>_api` copy of every app_rw policy (`migrations/0101_service_roles.sql:64-69`, `migrations/0101_service_roles.sql:79-133`) | api IAM user |
| `app_worker` | member of app_rw, INHERIT, no SET (`migrations/0101_service_roles.sql:56`) | worker and job IAM users |
| `app_sweeper` | EXECUTE on `runs_orphaned`, `runs_stalled`, `documents_orphaned`, `document_parts_orphaned`, `research_files_orphaned`, `operator_tenant_overview` only (`migrations/0101_service_roles.sql:67-69`, `migrations/0101_service_roles.sql:110-112`) | nobody yet: the sweeper runs inside the worker as app_worker (`migrations/0101_service_roles.sql:15-18`) |

- All three are created NOLOGIN NOBYPASSRLS, and the migration refuses an existing role with LOGIN, SUPERUSER, BYPASSRLS, CREATEROLE or CREATEDB (`migrations/0101_service_roles.sql:34-54`).
- `service_roles_sync()` rebuilds app_api's grants and policy copies from app_rw's catalog entries, then moves the IAM users named in `app.api_iam_users` / `app.worker_iam_users` from app_rw to their role. It refuses a superuser or BYPASSRLS user (`migrations/0101_service_roles.sql:61-150`).
- `migrations/env.py` hands `APP_API_IAM_USERS` / `APP_WORKER_IAM_USERS` to the migration as settings and calls `service_roles_sync()` after every migration run, so a later app_rw grant reaches app_api automatically (`migrations/env.py:86-109`).
- The Terraform migrate job passes those variables: api IAM user to app_api, worker and job IAM users to app_worker (`infra/stack/main.tf:746-751`).
- Code side: `DB_ROLES` maps `SERVICE_ROLE` api to app_api, worker and job to app_worker, migrate to none, all (dev) to app_rw (`src/account_intelligence/platform/gcp/adapters.py:271-278`). At cloud startup `assert_service_db_role` fails closed on another service's role and logs `db.role_pending` while the user is still in app_rw (`src/account_intelligence/db/session.py:142-149`).
- Status: ADR-0123 says the Terraform part is "planned, not applied" (`docs/decisions/ADR-0123-split-runtime-database-roles.md:3`). Until the migrate job runs with those variables, the services keep running as app_rw and log `db.role_pending`. Check with the `pg_auth_members` query at the end of the ADR.

## 4. Migrations

- Alembic `script_location = migrations` (`alembic.ini:2`). Each revision in `migrations/versions/NNNN_*.py` only reads and executes a SQL file `migrations/NNNN_*.sql` on upgrade and `NNNN_*.down.sql` on downgrade (`migrations/versions/0005_sources.py:15-21`, `migrations/versions/0101_service_roles.py:17-27`). The schema and its comments live in the SQL files.
- 35 revisions (0037 to 0072) open the SQL with a path relative to the working directory (`Path("migrations/...")`), so run Alembic from the repo root (`migrations/versions/0037_security.py:19`).
- Head revision on disk is **0102** (`migrations/versions/0102_document_members.py`, revises 0101). 0102 (zip members and document audience) belongs to the workspace-zip import work that another session has in progress, uncommitted. The last finished migration is 0101, the service-role split (`migrations/versions/0101_service_roles.py:11-12`). The last *committed* revision is 0097: 0098 to 0102 are uncommitted files (`git ls-files migrations/versions`).
- Number 0069 does not exist: 0070 revises 0068 (`migrations/versions/0070_definer_owners.py:12`).
- Readiness checks the schema. `/readyz` fails while the database revision is older than `EXPECTED_REVISION`, so a new revision never takes traffic before its migration has run. On disk the constant was "0101" earlier on 2026-11-03 and is now "0102", moved by the in-progress work (`src/account_intelligence/api/routes/health.py`, `EXPECTED_REVISION` and `revision_check`). Bump it with every new migration.
- Local: `make migrate` runs `alembic upgrade head` as the migrator; `make migration m="..."` creates a revision (`Makefile:61-65`). Cloud: the `migrate` Cloud Run job runs `alembic upgrade head` from the api image (`infra/stack/main.tf:736-741`).
- CI upgrades a fresh database to head, downgrades one step, upgrades again, runs `alembic check`, the RLS catalog check (`scripts/check_rls_catalog.sql`) and the isolation-matrix coverage check (`.github/workflows/ci.yml:101-119`).

## 5. Isolation tests (`tests/isolation`)

- Hard gate: `make test-isolation` runs `pytest tests/isolation -x` before the rest (`Makefile:83-88`). CI does the same (`.github/workflows/ci.yml:127-128`).
- The matrix: `scripts/gen_isolation_matrix.py` discovers every tenant table, view, SECURITY DEFINER function, `/v1`, `/operator` and `/tasks` route, storage layout, cache purpose, event type and job, and compares them with `tests/isolation/matrix/matrix.yaml`. A missing entry is UNCOVERED and an entry with no object is STALE; either fails `--check` (`scripts/gen_isolation_matrix.py:1-19`).
- `tests/isolation/test_matrix.py` plants canary rows for synthetic tenants A, B and C in every tenant table and, as tenant B, attempts every read, update, delete, write and move on A's canary. It also calls every route with the auth layer replaced by a fixed tenant-B principal, so the database must hold on its own (`tests/isolation/test_matrix.py:1-18`).
- `tests/isolation/test_service_roles.py` covers the 0101 roles: no LOGIN, SUPERUSER or BYPASSRLS; app_sweeper reads no table; app_api cannot insert into `tool_calls` or touch leases and checkpoint internals; the sync copies a later grant; IAM users move; a BYPASSRLS user stops the sync (`tests/isolation/test_service_roles.py:59`, `tests/isolation/test_service_roles.py:86`, `tests/isolation/test_service_roles.py:112`, `tests/isolation/test_service_roles.py:123`, `tests/isolation/test_service_roles.py:180`, `tests/isolation/test_service_roles.py:229`, `tests/isolation/test_service_roles.py:253`).
- Other files cover RLS, ownership, definer-function owners, people, jobs, spend, exports, grading, connector ledger and the app_rw IAM-user list (`tests/isolation`).
- In staging, a nightly `POST /tasks/isolation-check` runs `replica.isolation` (`src/account_intelligence/api/routes/tasks.py:561-562`), scheduled `0 2 * * *` UTC (`infra/stack/main.tf:807`). A failure emits `isolation_check_failed`, which has a log-based alert (`infra/modules/observability/platform.tf:48`).
- Test databases: the session fixture drops and recreates `TEST_DB` (default `ai_test`), so two pytest sessions on the same TEST_DB break each other (`tests/conftest.py:21`, `tests/conftest.py:50-55`).

## Code vs docs

- The fix prompts (A to E) left the head at 0101; on disk the head and `EXPECTED_REVISION` now say 0102, from the in-progress zip-import migration (`migrations/versions/0102_document_members.py`).
- ADR-0123 says the import contract lets "17 modules" import `system_scope`; `pyproject.toml` lists exactly those 17 in `ignore_imports`. The parked `contracts`, `quality` and `privacy` packages are no longer named in any contract (`pyproject.toml`).
- Migration numbering skips 0069. Do not assume `NNNN` equals the revision count.
