# Agent 4 backend-hardening: report

Branch `agent/backend-hardening` (worktree `/home/sakib/abm-agent-backend-hardening`, made from
`feat/replica` at a182f56). Test database `ai_agent_backend_hardening_test` only. No paid model or
provider call, no container, image, volume, gcloud or terraform command, no `.env` file opened.
Every test ran with the fake model and the hermetic providers.

Migrations added: **0070** (definer owners, M1), **0071** (IAM users into `app_rw`, L16),
**0072** (`app_rw` may read `alembic_version`, readiness). Head is now 0072.

## 1. Items

Status words: **fixed**, **partly** (what is left and why), **not done** (why).

### Security audit

| Item | What changed (file:line) | Proof (test) | Status |
| --- | --- | --- | --- |
| H1 research files parsed in the API with no scan or sandbox | The whole older path is removed (D1 below). Nothing parses an upload outside the documents sandbox any more: the only parser caller is `documents/sandbox_child.py`. | `tests/isolation/test_matrix.py` coverage (no `/v1/files*` route, no `/tasks/file-process`); `tests/system/test_single_write_paths.py` | fixed |
| M1 cross-tenant SECURITY DEFINER functions owned by the migration login | `migrations/0070_definer_owners.sql`: roles `sweeper_definer` and `tombstone_writer` (NOLOGIN, NOBYPASSRLS, NOINHERIT, created only when missing); a `FOR SELECT ... USING (true)` policy `sweeper_read` on the 10 tables the 11 lookups read, column grants on exactly the columns they use; `write_platform_tombstone` owned by `tombstone_writer` with INSERT and the conflict-target columns only. For a non-superuser migrator (Cloud SQL) the block grants itself membership and CREATE on `public` for the `ALTER FUNCTION ... OWNER` statements only and revokes both after. `.down.sql` hands the functions back. | `tests/isolation/test_definer_owners.py`: every definer function's owner is NOLOGIN, not superuser, not BYPASSRLS; each of the 11 lookups returns the rows of two synthetic tenants when called as `app_rw` with no context (the rows come from the owner's policies alone); the tombstone writer reads no table; the sweeper owner has no table grant and no content column | fixed (staging must confirm the temporary grants work for the Cloud SQL migrator: V-SQL-1; 0017 and 0021 already use `ALTER FUNCTION ... OWNER` and face the same rule) |
| M2 sandbox blocks network only with audit hooks | `src/documents/sandbox_child.py:114` `deny_network_and_processes()`: after `prctl(PR_SET_NO_NEW_PRIVS)` (no privilege, works as uid 10001), a raw classic-BPF seccomp filter installed with `seccomp(SET_MODE_FILTER, TSYNC)` through the C library (`ctypes`): EPERM for `socket`, `socketpair`, `connect`, `bind`, `listen`, `accept`, `accept4`, `execve`, `execveat`, `fork`, `vfork`, `ptrace`, `io_uring_setup` and any `clone` without `CLONE_THREAD`; `clone3` answers ENOSYS (glibc falls back to `clone` for threads); a foreign ABI (x32, i386) kills the process. x86-64 and aarch64. It runs after the file is opened and before the audit hook, which stays as the second layer. Fail closed on Linux; other systems keep the hook only. **No new dependency** (libseccomp/pyseccomp were not needed). `RLIMIT_NPROC` was not set: it counts threads, so 0 or 1 would stop pyarrow's threads; the filter refuses processes instead. | `tests/documents/test_units.py::test_the_kernel_filter_refuses_a_native_socket_and_a_process_but_not_a_thread` (with no audit hook: a native `socket()` from libc gets EPERM, TCP and UDP; a connection to 169.254.169.254 fails; `fork` and `execv` fail; a thread starts) and the existing `test_the_parser_sandbox_has_no_network` (a real parse still works inside); every document test parses through it | fixed (VERIFY(V-SANDBOX-1): Cloud Run's sandbox accepts the filter with TSYNC; a refusal there stops parsing, it never runs unfiltered) |
| M4 no per-user rate limits | `config/rate_limits.yaml` (reviewed values), `src/identity/rate_limit.py:126,152` `user_limits()`, `consume_user()` (shared Redis bucket in the cloud, in-process locally, 503 fail closed on Redis errors), `src/account_intelligence/api/ratelimit.py` `limit_user()`; called first in `start_upload` (`document_upload`), `retry_part` (`part_retry`), `resume_research` (`research_resume`), `start_everything` (`download_everything`), `create_export` (`export`). File Ask (`/v1/files/{id}/ask`) and file uploads/resume are gone with D1, so there is nothing left to limit or count against the chat quota there. | `tests/api/test_user_rate_limits.py` (values reviewed; a bucket refuses past capacity with 429 and Retry-After, per user, per client, per action; each of the five handlers takes one token, AST check) | fixed |
| M5 invalid CSRF key secret id | `src/account_intelligence/api/tenant_plane.py:37` `CSRF_KEY_REF` from `CSRF_KEY_SECRET` (registered in `config.py`, default `csrf-signing-key`, Agent 3's secret name); `:207` `assert_signing_keys()` checks the id shape and reads the key (>= 32 bytes) at API start in staging and production (`api/main.py` lifespan), so a missing secret stops the deploy. The unused `DOWNLOAD_KEY_REF` was also renamed to a valid id. | `tests/unit/test_csrf_key_secret.py` | fixed |
| L7 unbounded Argon2 threads | `src/identity/local_password.py:159` `HASH_CONCURRENCY = 4`, an anyio `CapacityLimiter` for hash and verify | `tests/unit/test_local_password.py::test_at_most_four_argon2_computations_run_at_once` | fixed |
| L8 client admin can switch admin MFA off | `src/identity/broker.py:399` `admin_mfa = policy.admin_requires_mfa or conn.require_admin_mfa` | `tests/phase3/test_ws2_sso_api.py::test_sign_in_is_refused_for_every_unproven_identity` (new case: connection flag off, admin without MFA, refused) | fixed |
| L9 no step-up for manage_settings and /cap/* | `config/roles.yaml` `step_up` adds `manage_settings` (version 1.1.0); `src/account_intelligence/api/policy.py:94,106-108`: `PUT /v1/documents/policy` is a `manage_settings` action, `/cap/approve`, `/cap/reject`, `/cap/extend` are `approve_cap` (they stay until FIX_PROMPT Phase 6) | `tests/phase3/test_ws2_sso_api.py::test_settings_and_cap_decisions_need_a_fresh_step_up` | fixed |
| L10 uvicorn access log with `?token=` | `src/account_intelligence/api/main.py:263` `_log_request`: one `http.request` line per request through the allow-list logger (method, route template, status, latency, request id; never the path's values, query, headers or body; probes skipped); `method` added to `config/telemetry/logs.yaml` | `tests/api/test_api_hardening.py::test_the_request_log_has_the_route_template_and_never_the_query` | fixed in the app; **handoff** for `--no-access-log` (section 4) |
| L12 `/docs`, `/openapi.json` in every profile | `src/account_intelligence/api/main.py:311`: no docs, redoc or schema route when `APP_ENV` is staging or production; `app.openapi()` (the snapshot) unchanged | `tests/api/test_api_hardening.py::test_the_cloud_profiles_serve_no_docs_or_schema`, `test_the_local_profiles_keep_the_docs` | fixed |
| L13 dev/test on Cloud Run | `src/account_intelligence/config.py:502` `deployment_refusals()`: `APP_ENV` dev or test with `K_SERVICE` or `CLOUD_RUN_JOB` is refused in `load_settings` and in `build_container` (`app/container.py`); both names registered | `tests/unit/test_env_registry.py::test_a_local_profile_is_refused_on_cloud_run` (4 cases) | fixed |
| L14 route probe without 404 and without a same-tenant user | `tests/isolation/test_matrix.py`: a tenant probe naming one of A's rows must answer 404, or exactly the status and problem type the route gives for an identifier that names no row (so the reply cannot tell the row exists; never a 2xx); new `test_routes_refuse_another_user_of_the_same_client` runs every route whose path names a row of an owned table (`config/ownership.yaml`) as `matrix-a2`, another admin of tenant A, on a world of its own, with the same rule and no owned value leaked. Finding fixed on the way: `GET /v1/connectors/{id}/quarantine` answered 200 with an empty list for another client's connection; it now answers 404 (`connectors_client/quarantine.py` `listing`, `routes/connectors.py`). | 251 route probes pass (201 cross-client, 50 same-client) | fixed |
| L17 IdP DNS rebinding, any port | `src/identity/ports.py:71` `PinnedBackend` (an httpcore network backend that resolves, checks every address and connects to the checked address in one step; port 443 only), `:111` `pinned_client()` used by `cloud_plane`; `require_public_https` refuses ports other than 443. `httpcore` declared in `pyproject.toml`. | `tests/phase3/test_ws2_tokens.py::test_idp_connections_go_to_the_address_that_was_checked` and the existing SSRF test | fixed |

### Production guards (one test each; extended `test_cloud_profile_refuses_local_switches`)

All in `tests/unit/test_env_registry.py`, both cloud profiles each:

| Guard | Where | Test parameter |
| --- | --- | --- |
| `LLM_FAKE` | registry `forbidden_in=CLOUD` (existing) and `container.py` (existing) | `("LLM_FAKE", "hermetic")` |
| `CONNECTORS=hermetic` | new rule in `Settings._profile_rules` (`config.py:442`); `container.py` already refused | `("CONNECTORS", "hermetic")` |
| `ALLOW_UNAUTHENTICATED_TASKS` | existing | `("ALLOW_UNAUTHENTICATED_TASKS", "1")` |
| any `DEVSTORE_*` (even a default value) | `deployment_refusals` | `DEVSTORE_ROOT`, `DEVSTORE_SESSION_TTL_S=172800`, `DEVSTORE_PUBLIC_URL` |
| the demo seed and every seed path | `scripts/seed_replica_demo.py`, `scripts/seed_pilot.py` also refuse `K_SERVICE` / `CLOUD_RUN_JOB` (they already required `APP_ENV` dev, local or test) | `test_the_demo_seeds_refuse_the_cloud` (8 cases, exit 2) |
| the fake IdP | `JWKS_URL`, `JWT_ISSUER`, `STAFF_JWKS_URL`, `STAFF_JWT_ISSUER` must be https and not a local or `fake-idp` host in the cloud (`config.py:499`); this also catches the registry's `http://localhost` default for `JWKS_URL` | `JWKS_URL=http://fake-idp...`, `JWT_ISSUER=https://localhost...`, `STAFF_JWKS_URL=http://fake-idp...` |
| `MOONSHOT_API_KEY` in env | existing `forbidden_in=CLOUD` | `("MOONSHOT_API_KEY", ...)` |
| `APP_ENV` dev or test on Cloud Run | L13 above | `test_a_local_profile_is_refused_on_cloud_run` |

Values never appear in the refusal (asserted).

### Health, readiness, SIGTERM

- `/healthz` liveness and `/readyz` readiness existed for the API and the worker (the same app;
  `SERVICE_ROLE` picks the sweeper). Added: readiness check `migrations`
  (`api/routes/health.py:39,49`): the database's revision must be at or after
  `EXPECTED_REVISION` (`0072`), so a revision deployed before its migration job ran never takes
  traffic; migration 0072 lets `app_rw` read the one-row `alembic_version`. Test:
  `tests/system/test_wiring_contract.py::test_readiness_needs_the_schema_revision_of_the_code`
  (the constant must equal the newest migration).
- SIGTERM flips `/readyz` to 503 (existing, tested in `tests/system/test_wiring.py`). The API's
  graceful shutdown is uvicorn's; `docker/api.Dockerfile` sets `--timeout-graceful-shutdown 20`,
  longer than Cloud Run's 10 s window: **handoff** (section 4). An in-flight slice cut off is
  redelivered by Cloud Tasks (checkpoint and ledger replay).
- The cloud api's readiness now reports provider keys as "checked on the worker" (they are mounted
  on the worker only), not "unconfigured" (Agent 3 item 13). Test:
  `test_the_cloud_api_says_provider_keys_are_checked_on_the_worker`.

### Database pool arithmetic

Every process: `DB_POOL_SIZE + DB_MAX_OVERFLOW` engine connections; the Cloud SQL engine now
honours both (`platform/gcp/adapters.py` `cloud_sql_engine(..., pool_size, max_overflow)`, it used
SQLAlchemy's 5 + 10 before; test `tests/unit/test_cloud_tasks_queues.py::test_the_cloud_sql_engine_uses_the_configured_pool`).
A worker also holds one checkpointer connection per in-flight slice (concurrency 4). With Agent 3's
numbers (ARCHITECTURE.md section 3; the stack sets 5 + 5):

| | staging | production |
| --- | --- | --- |
| api: max x (5 + 5) | 3 x 10 = 30 | 10 x 10 = 100 |
| worker: max x (5 + 5 + 4) | 2 x 14 = 28 | 8 x 14 = 112 |
| jobs (migrate, nightly): 2 x 5 | 10 | 10 |
| operators, Query Insights, maintenance | 15 | 15 |
| **total** | **83** | **237** |
| `max_connections` | 200 (db-custom-1-3840) | 400 (db-custom-2-7680) |

Written into the registry comment and descriptions (`config.py:124`), so a change of either value
shows the sum to redo. The registry defaults (10 + 5) are local defaults; the stack sets 5 + 5.

### Security headers

The API middleware already set nosniff, `X-Frame-Options: DENY`, `default-src 'none';
frame-ancestors 'none'`, `Referrer-Policy: no-referrer` and `Cache-Control: no-store` on every
response (authenticated or not). Added HSTS (`max-age=31536000; includeSubDomains`) when the
request came over HTTPS (scheme or `X-Forwarded-Proto`), in `api/main.py:288`
`security_headers()`. Test: `tests/api/test_api_hardening.py::test_every_api_response_carries_the_security_headers`.

### Dead code (DEAD_CODE.md)

| Item | What changed | Status |
| --- | --- | --- |
| D1 backend | Deleted `api/routes/research_files.py`, `research_files/{analytics,modelsvc,pipeline,profile,qa,service,upload,repo}.py` (all of `repo.py`: the one function documents used moved to `documents/repo.py`, see S5), `/tasks/file-process` and `FileTask`, the file sweep (`api/main.py`), `enqueue_file` (hermetic, in-process and Cloud Tasks schedulers), `container.files` and `file_services`, the `file` owner kind, the 12 `/v1/files*` policies, the `File*` contract models, `task.file_process` and `file_sweep` (`tenancy/catalog.py`), the matrix entries; `config/research_files.yaml` and its typed view keep only what the parsers read; the old key helpers `file_key`, `part_key`, `upload_prefix`. Tests deleted: `tests/research_files/{test_api,test_pipeline_db,test_large_file,kill_worker,large_driver}.py`; `world.py` keeps `admin`. Kept: `blobstore`, `checks`, `config`, `detect`, `parsers/*`, `test_parsers.py`, `fixtures.py`, the `research_file*` tables and `research_files_orphaned` (its matrix proof now points at `tests/isolation/test_definer_owners.py`). OpenAPI, the isolation matrix, `test_single_write_paths.py`, `docs/WIRING.md`, `docs/architecture/loops.md` and `docs/architecture/module-map.md` regenerated or updated. The guarantee tag in `parsers/media.py` now names the surviving parser test. | fixed |
| D10 `scripts/deploy_to_cloud_run.sh` | deleted; `tests/system/test_wiring_contract.py` now scans `scripts/deploy*.sh` | fixed |
| also (lead's request) `scripts/deploy_gcp.sh` | deleted; `docs/DEPLOY.md` and `docs/STATUS.md` no longer name it | fixed (workflows: handoff) |
| D12 `config/{dev,staging,production,storage}.yaml` | deleted | fixed |
| D14 old model router | `src/account_intelligence/model_routing.py` keeps `PipelinePhase`, `ModelDefinition`, `select_model`, `validate_model_id`, `get_model_router`; cost, limits, quality checks, region, retention, artifact path and their types removed with their tests (`tests/unit/test_model_routing.py`) | fixed |
| D25 `scripts/restore_from_backup.md` | deleted | fixed |
| S12 / D21 pointer | `config/section11_spec.yaml` QS.three_layers names `src/account_intelligence/workflow/stages/core.py` (the quality gate, step 12) instead of the missing `src/gate/pipeline.py` | fixed |

Note: commits 11aae0e and 349d273 also carry deletions that were staged with `git rm` before
them (D10, D12, D25 in 11aae0e; `scripts/deploy_gcp.sh` in 349d273); the history was not rewritten.

### Structure items and the rest

| Item | What changed | Proof | Status |
| --- | --- | --- | --- |
| S5 embedding calls' owner | `documents/repo.py:785` `log_embedding_call()` writes `research_file_model_calls` with the document's owner read in the same statement (the table's owner trigger looks for a `research_files` parent, which a document never has, so the row used to take the acting user, `test-owner` in the tests, and none for a system job) | `tests/documents/test_documents_db.py::test_file_upload_runs_through_scan_parse_and_index` asserts the owner equals the document's | fixed |
| S9 broad excepts | a reason comment on all 29 `except Exception` / `BaseException` that had none (`library.py` and `narrative.py` say why a failure is "no narrative" or "unsupported"); guard test so it stays so | `tests/system/test_static_guards.py::test_every_broad_except_says_why` | fixed |
| S8 SQL in route handlers | moved: `routes/credits.py` (account label, last finished, notifications: `replica/credits.py`, `replica/notifications.py`), `routes/spend_v2.py` (`runs/lifecycle.waiting`, `spend/prepulled.intent_events`) | existing route tests | **partly**: `routes/auth.py` (17), `runs.py` (15), `admin_console.py` (14), `accounts.py` (7), `research.py` (5), `account_workspace.py` (4), `tasks.py` (2) are left. Each is a behaviour-neutral move of many statements across the busiest routes; done in this wave it would collide with Agent 6's and Phase 5 to 7 work. Recommended as its own change after the merges. |
| S4 hard-coded values | registry entries `MOONSHOT_BASE_URL`, `GMAIL_API_BASE`, `GOOGLE_DRIVE_API_BASE`, `MICROSOFT_GRAPH_API_BASE` (https in the cloud), `CSRF_KEY_SECRET`, `TASKS_QUEUE_DOCUMENTS`, `TASKS_QUEUE_JOBS`, `BLOB_MOUNT_DIR`, `APP_RW_IAM_USERS`, `K_SERVICE`, `CLOUD_RUN_JOB`; `.env.example` names only; `docs/ENVIRONMENT.md` regenerated. The `us-central1` region left with D14. Kept on purpose: the Google issuer aliases in `api/operator.py:81-82` (a protocol constant of Google's tokens, not deployment config). No model id is in code. | `test_provider_api_bases_come_from_the_registry_and_are_https_in_the_cloud` | fixed |
| Config paths in the image (lead, from Agent 5) | every config file is found through `CONFIG_DIR` (`config.py`) or `config_dir()` (read at call time): `replica/credits.py`, `replica/jobs.py` (also the skill package, now `CONFIG_DIR/skills/abm-account-research`, same "not installed" answer as before), `connectors/hermetic.py`, `alerts/rules.py` (the skill asset ships as `config/alert_rules.yaml`, a byte-for-byte copy, tested), `quality/regression.py`, `privacy/{minimise,retention,dsar}.py`, `parallel/config.py`, `obs/catalog.py`, `llm/config.py`, `verify/config.py`, `evalgate/config.py`, and the cwd-relative `admin_console.py` reads | `tests/system/test_static_guards.py::test_no_module_finds_repository_files_relative_to_its_own_file` (fails on `parents[n>=1]`, `.parent.parent` or `Path("config/...")`; the doc generators and the conformance check are listed with reasons), `test_the_alert_rules_copy_matches_the_skill_asset` | fixed |

### Agent 3 (gcp-infra) handoffs

| # | What | Where | Proof | Status |
| --- | --- | --- | --- | --- |
| 1 | CSRF key id | M5 | M5 | fixed |
| 2 | pool bounds on the Cloud SQL engine | `platform/gcp/adapters.py` | pool test | fixed |
| 3 | three queues, dispatch deadlines | `CloudTasksScheduler`: slices and approval timeouts on `TASKS_QUEUE` (600 s), documents on `TASKS_QUEUE_DOCUMENTS` (1800 s, the maximum), research jobs and Download everything on `TASKS_QUEUE_JOBS` (600 s); unset names fall back to `TASKS_QUEUE` | `tests/unit/test_cloud_tasks_queues.py` | fixed |
| 4 | nightly CLI never bound the Cloud SQL engine | `db/session.py:65` `bind_cloud_engine()` shared by the API lifespan and `worker/__main__.py`. Found on the way: the worker's sweepers (`sweep_once`, `_sweep_documents`, `_mark_stalled`) passed `SessionLocal`, the unbound placeholder engine in the cloud, so every sweep pass would have failed there; they now use `session_factory()` (the bound engine) | `tests/worker/test_nightly.py::test_in_the_cloud_the_command_binds_the_cloud_sql_engine_first` | fixed |
| 5 | IAM users into `app_rw` | migration 0071 (`APP_RW_IAM_USERS`, comma separated, skips names not yet created, refuses a superuser or BYPASSRLS role, grants membership `WITH INHERIT TRUE` and nothing else, idempotent, no-op when unset) | `tests/isolation/test_app_rw_iam_users.py` | fixed |
| 6 | `runs.stalled` event | `api/main.py:200` (with `count`), in the extracted `sweep_pass()` | `tests/api/test_api_hardening.py::test_a_sweeper_pass_logs_runs_stalled_with_a_count` | fixed |
| 7 | lifecycle prefixes | new quarantine keys are `quarantine/tenants/<tenant>/<document>/raw` (`documents/service.py:114`); `tenancy/storage.py` resolves `quarantine/tenants/` and `uploads/tenants/` to their tenant and `tenant_prefixes()` lists every prefix of a tenant for offboarding; older rows keep their stored keys. There are no resumable "part" objects any more (D1 removed the only writer; the browser writes straight to the quarantine object). | `tests/unit/test_storage_prefixes.py`, the matrix storage probe, the documents tests | fixed |
| 8 | one part per Cloud Tasks request | `DocumentService.parts_per_request` (1 in the cloud profiles, unlimited locally): a request reads one part, queues the next task when parts are left, the last one decides the document; the sandbox wall clock and CPU are now 1500 s (`config/research_input_modes.yaml`) | `tests/documents/test_parts.py::test_one_part_per_request_hands_the_rest_to_new_tasks` | fixed |
| 9 | no whole-file temp copy | `GcsBlobStore(..., mount_dir)` gives `path(key)` on the read-only bucket mount, so the sandbox reads in place (`research_files/blobstore.py:228`); the worker refuses to start in the cloud without `BLOB_MOUNT_DIR` (`app/container.py:659`), so the 20 GiB limit stays (no 2 GiB cap was needed) | `tests/unit/test_blob_mount.py` | fixed |
| 10 | Phase 5 schedule routes | `/tasks/sweep`, `/tasks/retention`, `/tasks/alert-digest`, `/tasks/audit-verify` do **not** exist. The `/tasks` routes are: `run-slice`, `approval-timeout`, `document-process`, `download-everything`, `research-job`, `schedules-tick`, `maildrive-sync`, `access-expiry`, `isolation-check`. The orphan sweep runs inside the worker (`SERVICE_ROLE=worker`), not as a task. Those four schedules must stay paused until FIX_PROMPT Phase 5 writes the routes (5.4, 5.7, 5.3, 5.6). | | confirmed |
| 13 | readiness provider words on the api | see Health | test | fixed |

### Dependencies

`pip-audit` had found 0 vulnerabilities. Patch and minor upgrades with `uv lock --upgrade-package`
(no major): aiohttp 3.14.5, dnspython 2.9.0, filelock 4.1.2, google-api-core 2.42.0,
google-auth 2.62.0, google-cloud-storage 3.17.0, iniconfig 2.3.1, jsonpatch 1.35,
jsonpointer 3.2.1, langchain-core 1.6.10, langgraph 1.2.15, langgraph-checkpoint 4.3.0,
langgraph-checkpoint-postgres 3.1.3, langgraph-sdk 0.4.7, langsmith 0.14.8, markupsafe 3.0.4,
opentelemetry-api and -sdk 1.45.1 (semantic-conventions 0.66b1), platformdirs 4.13.0,
python-discovery 1.6.2, sqlalchemy 2.1.4, truststore 0.10.5, virtualenv 21.14.7. Not upgraded on
purpose: fastapi, starlette, pydantic(-core, -settings), anthropic, asyncpg, ruff, mypy, orjson,
websockets, multidict, uuid-utils and the other minor bumps of frameworks and tools whose new
minor versions change behaviour or lint output; they need their own change. `httpcore>=1.0` is now
declared (it was already installed through httpx).

## 2. Checks

Full suite, one process, on the final tree:
`PGPASSWORD=dev PGHOST=localhost PGPORT=5433 TEST_DB=ai_agent_backend_hardening_test uv run --frozen pytest -m "not eval" -q -p no:cacheprovider tests`
gave **4327 passed, 14 skipped, 1 failed** in 19 min 28 s. The one failure is
`tests/api/test_openapi_snapshot.py::test_the_generated_web_client_has_no_drift`: `web/lib/gen/`
is the lead's and still holds the removed `/v1/files*` operations (section 3); it passes once the
lead regenerates the client. (The run before it had two more failures, both fixed: the fuzz
admin had no fresh step-up after L9, and `.env.example` lacked `K_SERVICE` and `CLOUD_RUN_JOB`.)
The isolation suite (`tests/isolation`, 823 tests including the 251 route probes and the new
definer-owner and IAM-user tests) is part of that run.

| Check | Command | Result |
| --- | --- | --- |
| route catalog | `uv run --frozen python scripts/check_route_catalog.py` | exit 0, "every route has a policy, an audit class and a session tenant" |
| guarantees | `uv run --frozen python scripts/check_guarantees.py` | exit 0, 429 tags, 0 dangling, 0 untagged guards |
| routing governance | `uv run --frozen python scripts/check_routing_governance.py` | exit 0 |
| comments | `uv run --frozen python scripts/check_comments.py` | exit 0 |
| loop inventory | `uv run --frozen python scripts/gen_loops.py --check` | exit 0 (regenerated `docs/architecture/loops.md`) |
| environment docs | `uv run --frozen python scripts/gen_env_docs.py --check` | exit 0, "docs/ENVIRONMENT.md is current" (regenerated) |
| OpenAPI | `uv run --frozen python scripts/export_openapi.py` then `--check` | exit 0, "openapi snapshot OK" |
| isolation matrix | `uv run --frozen python scripts/gen_isolation_matrix.py --check --dsn postgresql://postgres:dev@localhost:5433/ai_agent_backend_hardening_test` | exit 0, "every discovered object is covered" |
| migration lint | `uv run --frozen python .claude/skills/account-intelligence-platform/scripts/lint_migrations.py migrations` | exit 0, 140 files pass |
| wiring audit | `uv run --frozen python scripts/wiring_audit.py --check` | exit 1 only for "generated web client drift" (the lead regenerates `web/lib/gen/`); "wiring audit report OK", `docs/WIRING.md` regenerated |
| lint, format, types | `uv run --frozen ruff check src tests scripts`, `ruff format --check`, `mypy src` | all checks passed; 1071 files formatted; mypy no issues in 647 files |

## 3. Generated client: operations that disappear

`web/lib/gen/` was not edited. Regenerating it (`scripts/gen_web_client.py`) drops these
operations: `start_upload_v1_files_uploads_post`, `upload_state_v1_files_uploads__upload_id__get`,
`upload_part_v1_files_uploads__upload_id__parts__part_no__put`,
`complete_upload_v1_files_uploads__upload_id__complete_post`, `list_files_v1_files_get`,
`file_status_v1_files__file_id__get`, `file_audit_v1_files__file_id__audit_get`,
`file_analytics_v1_files__file_id__analytics_get`, `ask_file_v1_files__file_id__ask_post`,
`approve_file_v1_files__file_id__approve_post`, `resume_file_v1_files__file_id__resume_post`,
`delete_file_v1_files__file_id__delete`; and the schemas `FileAnalytics`, `FileAnswer`,
`FileAudit`, `FileCitation`, `FileFinding`, `FileList`, `FileState`, `FileStatus`, `FileSummary`,
`FileTask`, `FileUploadCompleted`, `FileUploadPart`, `FileUploadStarted`, `FileUploadState`,
`Question`, `UploadInit`. No operation was added or changed in shape. Until then
`tests/api/test_openapi_snapshot.py::test_the_generated_web_client_has_no_drift` fails on this
branch (expected).

## 4. Handoffs

- handoff: `docker/api.Dockerfile:33-34` CMD: add `"--no-access-log"` (L10; the app's own
  `http.request` log replaces it) and change `--timeout-graceful-shutdown` from `20` to `8`
  (inside Cloud Run's 10 s SIGTERM window). Same `--no-access-log` for the worker's uvicorn
  command (`docker/worker.Dockerfile` or the Cloud Run worker entry point) and the compose worker
  entry point.
- handoff: `.github/workflows/deploy-staging.yml:45` and `deploy-prod.yml:40` still run
  `bash scripts/deploy_gcp.sh`, which is deleted (Agent 5's workflows deploy by digest).
- handoff (infra, Agent 3 / lead): the worker service must have `BLOB_MOUNT_DIR` (it now refuses to
  start without it in the cloud); the migrate job needs `APP_RW_IAM_USERS`; api, worker and the
  nightly job `TASKS_QUEUE_DOCUMENTS` and `TASKS_QUEUE_JOBS`; the api `CSRF_KEY_SECRET` (all
  already in Agent 3's design).
- handoff (lead, staging RUNBOOK): V-SANDBOX-1 (seccomp TSYNC accepted on Cloud Run) and the M1
  owner change under the non-superuser migrator (V-SQL-1).
- handoff (lead, docs): `DEPLOYMENT.md:67` (D10), `docs/infrastructure-plan.md:3` (D12 configs),
  `docs/build/{BUILD_PROMPT,PLAN,PHASE_4B_PLAN,UNDERSTANDING}.md` name `/v1/files` and
  `research_files` modules (plans, left as written).
- handoff (lead): regenerate `web/lib/gen/` (section 3); `docs/ENVIRONMENT.md`,
  `docs/WIRING.md`, `docs/architecture/loops.md` and `module-map.md` are already regenerated here.
- handoff (Agent 6 / lead): the web no longer has any API under `/v1/files*` (D1w removes the web
  half).

## 5. Docs edited because they named removed code

`docs/DEPLOY.md` (manual deploy script), `docs/STATUS.md` (same), and the generated
`docs/api/openapi.json`, `docs/WIRING.md`, `docs/architecture/loops.md`,
`docs/architecture/module-map.md`, `docs/ENVIRONMENT.md`.
