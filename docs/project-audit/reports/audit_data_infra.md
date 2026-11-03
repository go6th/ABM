# Audit: data, infrastructure and dependencies

Repo: `/home/sakib/ABM Generator` (GitHub remote `go6th/ABM`, branch `feat/final-product`, 2026-11-03).
Method: read-only (grep, wc, git log, file reads). Nothing run, deployed or connected to a database or cloud.
Real `.env` and `infra/envs/staging/terraform.tfvars` were NOT opened (they exist, are git-ignored; only `.env.example`, `terraform.tfvars.example` and code were read).
"Unverified" = I did not or could not confirm it from code alone.

Contents: 1 Python stack, 2 Database, 3 Infrastructure, 4 Security, 5 External providers, 6 Tests, 7 Repo hygiene, 8 Doc-vs-code drift, 9 Summary.

---

## 1. Python stack

### 1.1 Toolchain

| Item | Value | Notes |
|---|---|---|
| Python | `requires-python >=3.11,<3.13`; `.python-version` = 3.12; images `python:3.12-slim@sha256:...` | ruff targets py311, mypy targets 3.12 (numpy stubs need 3.12) |
| Package manager | `uv` (lock: `uv.lock`, 579 KB; `uv sync --frozen`); images pin `uv:0.9.5` | Build backend hatchling; wheel packages list is 38 top-level packages under `src/` |
| Dependency groups | `dev` (default), `cpu` (default, `fastembed==0.9.1`), `gpu` (`fastembed-gpu`, `onnxruntime-gpu[cuda,cudnn]==1.31.0`, `pandas==3.0.6`); `cpu` and `gpu` are declared conflicting | worker image chooses via `--build-arg UV_GROUP=gpu` |
| Lint | `ruff` line length 100, rules E,F,I,B,UP,SIM; excludes `.claude .agents _review docs config/skill/archive scripts/parity/parity_check.py` | `make lint` also runs `ruff format --check` |
| Types | `mypy --strict` over `src` with pydantic plugin; `lxml-stubs`, `types-*` | |
| Docstrings | `interrogate` fail-under 100 on `src` | strict: every public symbol needs a docstring |
| Security scan | `bandit` (skips B101, B311), `vulture --min-confidence 80`, gitleaks (pre-commit + CI) | |
| Tests | `pytest` (+asyncio, cov, hypothesis); coverage `fail_under = 94` with branch + greenlet | the in-file comment admits the 94 baseline predates 5 packages and was never re-measured (stale) |
| Mutation | `mutmut` (`make mutation`, ~15 min, 90 % bar) on ~40 pure-logic modules | not in default CI, unverified whether ever run in CI |
| Pre-commit | ruff, ruff-format, end-of-file, trailing-whitespace, gitleaks v8.24.2, custom migration-isolation lint | |
| Import-layer contracts | `import-linter` (`lint-imports`) 17 `forbidden` contracts, details in 1.3 | run by `make static` |
| Custom static checks (`make static`) | `check_comments.py`, `check_guarantees.py` (every `GUARANTEE(test_x)` must resolve to a test), `gen_loops.py --check` (every loop declares a `LOOP(...)` cap), `compileall` | unusual and strict discipline |
| Frontend | Next 16.4.1, React 19, TypeScript 6, vitest 5, Playwright 1.63, zod 4, react-markdown + rehype-sanitize, `@radix-ui/react-dialog`, lucide-react | `web/package.json`; Node 22 image |
| Extra Node toolchains | `render/docx` (TypeScript Word renderer on the `docx` npm package), `runtime/abm-render-tools` (node packages) | both baked into the Python images; LibreOffice Writer + Calc and poppler-utils also installed in images |

### 1.2 Runtime dependencies (pyproject.toml `[project].dependencies`, 40 entries; versions are lower bounds, uv.lock pins exact)

| Package | Constraint | Used for (verified by grep of `src/`) |
|---|---|---|
| fastapi | >=0.115 | HTTP API; 48 files; ~214 route decorators |
| uvicorn[standard] | >=0.30 | ASGI server (api + worker, 1 worker process per instance) |
| pydantic / pydantic-settings | >=2.7 / >=2.4 | models; typed env registry `config.py` |
| sqlalchemy[asyncio] | >=2.0 | 200 files; mostly raw `text()` SQL, only 5 ORM models |
| asyncpg | >=0.29 | async Postgres driver (runtime) |
| psycopg[binary,pool] | >=3.2 | sync/async driver for the LangGraph checkpointer and some tools |
| alembic | >=1.13 | migrations (wrappers execute plain `.sql` files) |
| cloud-sql-python-connector[asyncpg] | >=1.12 | Cloud SQL IAM auth + private IP in staging/prod (`platform/gcp/adapters.py`) |
| langgraph / langgraph-checkpoint-postgres | >=1.0 / >=2.0 | research graph + durable checkpoints |
| anthropic | >=0.40 | Claude API (direct or Vertex) |
| httpx / httpcore | >=0.27 / >=1.0 | all outbound HTTP; httpcore used directly for the pinned-IP IdP client |
| redis | >=5.0 | rate limits, provider cache index |
| google-auth | >=2.30 | verifying Cloud Tasks / Scheduler OIDC tokens |
| google-cloud-storage | >=2.18 | GCS blob store |
| google-cloud-tasks | >=2.16 | enqueue run slices |
| google-cloud-kms | >=2.20 | BYOK envelope encryption of tenant model keys (`chat/credentials.py`) |
| google-cloud-secret-manager | >=2.20 | secrets read at runtime |
| google-cloud-monitoring (==2.31.0), opentelemetry-api/sdk, opentelemetry-exporter-gcp-trace, opentelemetry-exporter-gcp-monitoring (==1.15.0a0, an alpha pin) | | tracing + custom metrics (`obs/export.py`, `METRICS_EXPORT=cloud_monitoring`) |
| pyjwt[crypto] | >=2.9 | tenant OIDC and staff Google token verification |
| signxml | >=4.0 | SAML signature verification (`identity/saml.py`); pulls lxml |
| argon2-cffi | >=23.1 | local password hashing (Argon2id) |
| openpyxl | >=3.1.5 | Excel read/write (26 files; deliverables, uploads) |
| python-docx | >=1.2 | Word generation/checks (`documents/gate.py`, `exports/word/render_cli.py`, `views/*`) |
| pypdf | >=5.0 | PDF text extraction (uploads) |
| pyarrow | >=17.0 | Parquet + tabular parsing in the sandbox (`research_files/parsers/tabular.py`, `documents/sandbox_child.py`) |
| pillow | >=11.0 | image handling: branding assets, media parser |
| jsonschema | >=4.23 | event/contract validation (`opendoor/events.py`, `replica/router.py`) |
| pyyaml | >=6.0 | all `config/*.yaml` (171 files import it) |
| python-multipart | >=0.0.9 | only referenced in `api/routes/sso.py` (SAML form POST); needed by FastAPI form parsing, unverified beyond that |
| pandas | >=2.0 | **No `import pandas` anywhere in `src/` or `scripts/`**: only comments mention it. Likely unused runtime dependency (candidate to remove). The `gpu` group also pins `pandas==3.0.6` "ad hoc". |
| fastembed (cpu group) / fastembed-gpu + onnxruntime-gpu (gpu group) | ==0.9.1 / ==1.31.0 | on-server embedder `BAAI/bge-small-en-v1.5` (384 dims), baked into image at `/app/models` |
| tiktoken | not declared (optional) | `rag/tokenizer.py` falls back when missing; mypy override `ignore_missing_imports` |
| numpy | not declared | transitive only (fastembed/pyarrow) |

Dev group: bandit, hypothesis, import-linter, interrogate, lxml-stubs, mypy, pre-commit, pytest, pytest-asyncio, pytest-cov, ruff, types-jsonschema, types-openpyxl, types-PyYAML, vulture. `mutmut` is fetched ad hoc (`uv run --with`).

Observations: no upper bounds on most runtime packages (only the lockfile protects you); two alpha/exact pins on the monitoring exporter; `apt-get -y upgrade` in the Dockerfiles makes image builds non-reproducible by design (to satisfy Trivy); LibreOffice in images is a large attack surface and image size.

### 1.3 Import-layer contracts (`[tool.importlinter]`, 17 contracts)

Root packages: all 38 `src/*` packages. Contracts (all `forbidden`):

| # | Rule (source must not import ...) |
|---|---|
| 1 | `runs`, `identity`, `metering`, `app` must not import `api` (ratchet: 5 `ignore_imports` entries that "may shrink, never grow", e.g. `app.container -> api.tenant_plane`, `identity.rate_limit -> api.problem`) |
| 2 | `platform, connectors, grading, tracker, workflow, runs, jobs, people, spend, exports, llm, verify, connectors_client` must not import `agents` (ABM agent layer orchestrates, never imported) |
| 3 | `platform` must not import connectors, tracker, grading, workflow, runs, api, app, jobs |
| 4 | `connectors` must not import tracker, grading, workflow, runs, api, app, jobs |
| 5 | `grading` must not import tracker, workflow, runs, api, app, jobs |
| 6 | `tracker` must not import workflow, runs, api, app, jobs |
| 7 | `jobs` must not import workflow, runs, api, app |
| 8 | `workflow`, `runs` must not import api, app |
| 9 | `spend` must not import api, app, runs, workflow (one exception `spend.plan -> workflow.spec`) |
| 10 | `exports` must not import api, app, runs |
| 11 | `platform, connectors, grading, tracker, jobs, people` must not import `exports` |
| 12 | `runs` must not import `workflow` (one-way) |
| 13 | `rag` must not import peer subsystems, workflow, runs, jobs, api, app, exports |
| 14 | `parallel` is a lower layer (no workflow, runs, api, app, tracker, grading, jobs, people, exports, spend) |
| 15 | `llm` is a lower layer (no workflow, runs, api, app, tracker, jobs, people, exports, spend, parallel, verify, evalgate) |
| 16 | `verify` and `evalgate` must not import workflow/runs/api/app (verify not evalgate) |
| 17 | pure/domain packages (`parallel, llm, verify, evalgate, spend, jobs, people, grading, tracker`) must not import `obs`; `obs` must not import api/app/workflow |

Not covered: `replica`, `chat`, `documents`, `research_files`, `views`, `admin`, `parity`, `exports -> documents` etc. have no contracts. The newest and largest packages (replica 14.5k lines, documents 10k) are unconstrained.

### 1.4 Makefile targets (key ones)

| Target | What it does |
|---|---|
| `install` | `uv sync --frozen` + pre-commit install |
| `up` / `down` / `reset` / `logs` | compose stack (hermetic); `reset` deletes volumes |
| `up-deps` / `emulators` | only db, gcs, tasks, redis (for backend tests) |
| `replica-up` / `replica-down` | `compose.replica.yml` stack (web :3010, api :8090) |
| `migrate` / `migration m=...` | alembic upgrade head / new revision |
| `run` | uvicorn with reload on :8080 using `.env` |
| `fmt`, `lint`, `types` | ruff, mypy, tsc |
| `static` | lint-imports, interrogate, comments, guarantees, loops, bandit, vulture, compileall |
| `test-isolation`, `test`, `check` | RLS isolation suite (hard gate), full tests `-m "not eval and not live"`, `scripts/check.sh` (full pre-PR gate on a scratch DB incl. `alembic check` and RLS catalog SQL) |
| `contract` | OpenAPI snapshot, generated web client, route catalog, wiring inventory (`--check`) |
| `infra-fmt`, `infra-validate`, `infra-test` | terraform fmt/validate/offline plan tests via a `hashicorp/terraform:1.9` container |
| `obs-verify` | telemetry contract, generated Terraform diff, runbook lint, canary suite |
| `image`, `smoke`, `release-check` | build 3 images; smoke non-root + /readyz + SIGTERM; Trivy + image inspection + deployed smoke |
| `deploy-staging` | PRINTS stale commands (see section 8) |
| `seed`, `e2e`, `load-smoke`, `load-staging`, `fuzz`, `backup-restore`, `mutation`, `doctor`, `env-check`, `verify`, `verify-wiring`, `scan`, `clean` | as named; `verify` is the aggregate local gate |

---

## 2. Database

### 2.1 Overview

* PostgreSQL 16 with **pgvector** (`pgvector/pgvector:pg16` locally; Cloud SQL Postgres 16 in cloud). Extension `vector` created in 0001/0002.
* **~205 tables** (211 `CREATE TABLE` statements counted in the non-down SQL, a few are re-creations or partitions) created across 96 migration files (`migrations/0001`..`0097`; number **0069 does not exist**; the chain 0068 -> 0070 is intact). Each migration = `NNNN_name.sql` (+ `.down.sql`) executed by a thin Alembic wrapper in `migrations/versions/NNNN_name.py`. Alembic only tracks revision order. Schema documents itself through SQL comments.
* ORM: only **5 SQLAlchemy models** (`db/models`: tenant, account, run, source, source_counter, ~324 lines). All other access is hand-written SQL via `text()` inside `tenant_txn`. `alembic check` compares only these modelled tables (so "models and migrations agree" covers a small part).
* `/readyz` refuses to serve unless the DB revision equals `EXPECTED_REVISION = "0097"` (`api/routes/health.py`); bump it after each migration.
* Numbering quirk: two branches each made a 0087 and 0088; staging ran them first, so later migrations were renumbered 0094/0095; migrations are written idempotent (`IF NOT EXISTS`) to cope.
* Rules enforced by tests/lint (`migrations/README.md`): every tenant table has non-null `tenant_id` and `FORCE ROW LEVEL SECURITY`; `app_rw` is never an owner and has no BYPASSRLS; ledgers are insert-only (no UPDATE/DELETE grant); one module writes each table (`config/ownership.yaml`).

### 2.2 Tables by domain (condensed; one-line purpose)

Counts are approximate (derived from `CREATE TABLE` in migrations).

**Tenants, users, auth, identity**

| Table | Purpose |
|---|---|
| `tenants` | a client organisation (keyed by `id`, not `tenant_id`) |
| `users`, `memberships`, `membership_events` | people in a tenant, role per membership, history |
| `tenant_events`, `tenant_settings_versions`, `client_settings`, `platform_defaults` | tenant lifecycle log, versioned settings, client limits, single-row platform defaults (operator-written) |
| `idp_connections`, `login_states`, `auth_sessions`, `auth_events`, `saml_assertion_ids` | SSO connections (OIDC/SAML), pending sign-ins, server-side sessions (SHA-256 of token), auth log, SAML replay protection |
| `local_identities`, `local_auth_tokens`, `local_auth_events` | first-party password accounts, single-use hashed verify/reset tokens, brute-force events (accessed only through definer functions) |
| `user_limits`, `usage_periods`, `limit_requests`, `user_preferences` | per-user quotas and requests, chosen model/effort |
| `support_grants`, `support_session_events`, `operator_audit`, `broker_keys`, `feature_flags`, `flag_events`, `quota_versions` | operator/support plane: time-boxed support access, staff actions, flags |
| `tenant_model_credentials` | tenant's own LLM key, KMS-envelope encrypted (BYOK) |
| `client_invites` (0060, via function), `ownership_backfill` | invites; record of the private-workspace owner backfill |

**Accounts, research runs, leases, durability**

| Table | Purpose |
|---|---|
| `accounts` | a researched company (owned by `added_by` user) |
| `account_runs` | **the live run table** (status machine QUEUED/RUNNING/DISTILLING/VERIFYING/..., budget, stop_detail, progress/stalled times) |
| `runs` | older data-layer run table (0004) still referenced by tracker/sources; `credit_cap` there is not enforced (ADR-0030): two run tables coexist |
| `run_leases` | per-run lease (one worker per run, heartbeat) |
| `research_jobs` | user-facing research requests: kind (new_research, web_refresh, scheduled_refresh, file_analysis, library_add), scope, goal, 1 job : 1 run |
| `run_cap_versions`, `run_cap_events`, `balance_snapshots`, `spend_reports`, `spend_defects`, `prepulled_files`, `prepulled_intents`, `prepulled_intent_events`, `spend_limit_events` | credit-cap approvals and spend reporting (insert-only) |
| `work_units`, `work_unit_events`, `provider_limits`, `provider_limit_events`, `provider_leases`, `provider_waiters` | concurrency governor: per-provider slot leases across all tenants (written only by `governor_*` definer functions) |
| `account_source_counters`, `account_bundles`, `download_jobs`, `link_archive` | per-account counters, delivered bundles, "Download everything" jobs, archive of fetched links |
| LangGraph `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` | created by the library's `setup()` (run as migrator), RLS keyed on `thread_id` prefix |

**Facts, evidence, grades, coverage**

| Table | Purpose |
|---|---|
| `facts` | graded claim about an account (`visibility` public/private; grade, status) |
| `fact_evidence`, `grade_events`, `claim_drops`, `fact_reevaluations` | evidence quotes per fact, grade history, dropped claims, re-grading |
| `sources` | cited sources per account/run |
| `run_coverage_meta`, `coverage_rows`, `coverage_row_events` | the "tracker": checklist rows and their state per run |
| `lead_verifications`, `lead_verification_queries` | blind verification of lead facts |
| `verifier_findings`, `abm_sense_checks`, `abm_distill_outputs`, `pull_manifests` | verifier output, sense checks, distilled narrative, what was pulled |
| `report_sections`, `deliverables`, `internal_deliverables`, `export_packages`, `export_verifications`, `view_rows`, `view_materializations`, `saved_views`, `branding_assets`, `run_gate_results`, `leak_checks` | generated sections, files (Word/Excel/zip), their verification, quality gate and leakage check results |

**Jobs and people pipelines, tech**

| Table | Purpose |
|---|---|
| `job_postings`, `job_posting_classifications`, `job_posting_observations`, `job_posting_platform_sentences`, `job_pipeline_runs`, `job_pipeline_passes` | job-posting pipeline (platform keyword/skill classification) |
| `people`, `person_sources`, `person_profiles`, `person_posts`, `person_post_pulls`, `person_events`, `person_search_pages`, `people_pipeline_runs`, `people_data_consents` | people pipeline (contacts, profiles, posts) |
| `tech_detections`, `tech_stack_items` | technology signals and the tech-stack workbook rows |

**Ledger, credits, billing, metering (money)**

| Table | Purpose |
|---|---|
| `tool_calls` (+ `tool_call_events`, `tool_call_records`) | **insert-only ledger of every paid provider call** (credits, USD, cost_source, payload ref) |
| `llm_calls`, `llm_call_attempts`, `llm_candidate_health`, `model_reply_log` | model call records, health of routing candidates, reply cache keyed by hash (research cache) |
| `credit_entries` | **the one credit ledger** (append-only) for the client's monthly pool (0057 made it the single truth) |
| `wallets`, `wallet_ledger` | legacy wallet; read-only for `app_rw` since 0057 |
| `usage_events`, `usage_counters`, `usage_thresholds`, `plan_limits`, `plan_assignments` | metering and plan limits |
| `monitoring_budget_versions`, `monitoring_usage`, `throttle_events`, `unit_cost_rollups`, `ops_cost_rollups`, `ops_rollup_watermarks` | monitoring budget and cost rollups |
| `notification_outbox`, `notification_deliveries`, `notifications` | outbox pattern for emails/notices; in-app notifications |

**Documents, chunks, uploaded files (RAG)**

| Table | Purpose |
|---|---|
| `research_files`, `research_file_uploads`, `research_file_upload_parts`, `research_file_chunks`, `research_file_state`, `research_file_findings`, `research_file_model_calls`, `research_file_deletions` | the first file-analysis subsystem (0051) |
| `documents`, `document_parts`, `document_passages`, `document_redactions`, `document_deletions`, `document_policies`, `run_documents`, `raw_files`, `library_uploads`, `library_accounts`, `library_publications` | the current upload/library model (parts for large files, text passages with `tsvector`, redactions, raw file store) |
| `document_tables`, `document_table_rows` | sheets stored as queryable tables for Ask |
| `chunks` | **vector table**: hash-partitioned (16) by tenant; `embedding halfvec(1536)` (hashing vectors used by the leak gate) + `semantic_embedding halfvec(384)` (bge-small, retrieval); HNSW index per partition; GIN on `tsv`; `visibility` CHECK = 'private' |

**Chat / Ask**: `chat_threads`, `chat_turns` (owner = `owner_user_id`; `chat_turns -> accounts, chat_threads`).

**Refresh, schedules, signals, alerts**

| Table | Purpose |
|---|---|
| `refresh_slots`, `refresh_runs`, `refresh_row_plans`, `signal_history`, `deltas`, `delta_items`, `missing_observations`, `shelf_life_overrides` | scheduled refresh planning and diffing; the schedule lives on accounts + `0055_schedule_lookups` definer functions |
| `monitor_configs`, `monitor_runs`, `webhook_receipts`, `signal_events`, `signal_event_evidence`, `signal_event_links`, `intake_drops` | signal monitoring and incoming events |
| `alerts`, `alert_outbox`, `alert_suppressions`, `subscriptions`, `tenant_rules`, `tenant_rule_accounts`, `alert_feedback`, `digest_runs` | alerts, rules, feedback, digest emails |

**Audit, privacy, security, ops, quality**

| Table | Purpose |
|---|---|
| `audit_events`, `audit_chain_checks` | tamper-evident audit log (see 2.5) |
| `retention_runs`, `retention_actions`, `tombstones`, `platform_tombstones`, `dsar_requests`, `dsar_events`, `suppression_entries` | retention, deletion tombstones, DSAR handling |
| `rotation_events`, `soc2_evidence`, `pentest_tracker`, `review_samples` | security/compliance registers (data tables; code behind them is stubbed, see 4.3) |
| `connector_connections`, `connector_oauth_states`, `connector_grants`, `connector_quarantine`, `connector_items`, `connector_access_log` | Gmail/Drive/Microsoft Mail + drive sync (per user OAuth) |
| `crm_connection_events`, `crm_mappings`, `crm_writes`, `crm_write_events` | CRM write-back (feature present in schema/code) |
| `ops_*`, `slo_snapshots`, `stuck_run_observations`, `canary_probe_results`, `run_anomaly_events`, `release_checks`, `analyst_label_tasks`, `analyst_labels`, `run_feedback`, `usefulness_events`, `eval_runs`, `eval_case_results`, `eval_metrics`, `eval_baselines`, `contract_*`, `link_checks`, `metric_snapshots`, `question_categories`, `isolation_checks`, `isolation_check_pair` | observability, quality, evals, nightly isolation check |

### 2.3 Entity-relationship summary (for a diagram)

Every tenant table has `tenant_id -> tenants.id`; I list only the other relations. Most child tables use **composite FKs** `(tenant_id, parent_id)` so a row can never point at another tenant's parent (a plain FK check bypasses RLS). Extracted from `REFERENCES` clauses; 177 tables carry at least one FK. Some tables (e.g. `work_units`, `tool_call_events`) reference only `tenants`.

Core spine:

```
tenants 1--* users, memberships, accounts, account_runs, research_jobs, documents, chat_threads, credit_entries ...
accounts 1--* account_runs (run per research)         account_runs *--1 accounts
account_runs 1--0..1 run_leases (by run_id)
account_runs self-ref parent (refresh -> run it refreshes); account_runs -> tenant_settings_versions
research_jobs *--1 accounts ; research_jobs 1--0..1 account_runs (unique tenant_id+run_id) ; credit_entries *--1 research_jobs
accounts 1--* facts ; facts 1--* fact_evidence ; facts 1--* grade_events ; facts 1--* fact_reevaluations
accounts 1--* sources ; sources *--1 runs
runs (legacy) 1--* coverage_rows 1--* coverage_row_events ; runs 1--1 run_coverage_meta
account_runs 1--* abm_distill_outputs, abm_sense_checks, pull_manifests, leak_checks, run_gate_results,
                 internal_deliverables, run_documents ; run_documents *--1 documents
accounts 1--* deliverables (self-ref: supersedes) ; accounts 1--* report_sections, export_packages->export_verifications
accounts 1--* job_postings 1--* job_posting_classifications, job_posting_platform_sentences
accounts 1--* job_pipeline_runs 1--* job_pipeline_passes ; accounts 1--* job_posting_observations
accounts 1--* people, person_sources, person_profiles, person_posts, person_post_pulls, person_events, person_search_pages, people_pipeline_runs
accounts 1--* tech_detections, tech_stack_items, link_archive, account_bundles, download_jobs
documents 1--* document_parts, document_passages, document_redactions, document_tables 1--* document_table_rows ; documents 1--* raw_files
library_uploads 1--* library_accounts 1--* library_publications
chat_threads 1--* chat_turns ; chat_turns *--1 accounts
research_files 1--* research_file_chunks, research_file_findings, research_file_state ; research_file_uploads 1--* research_file_upload_parts
refresh_slots *--1 accounts ; refresh_slots 1--* refresh_runs 1--* deltas 1--* delta_items ; refresh_runs 1--* refresh_row_plans, missing_observations
signal_events *--1 accounts, facts, delta_items ; signal_events 1--* signal_event_evidence, signal_event_links, alert_suppressions
alerts *--1 accounts, delta_items, signal_events ; alerts 1--* alert_outbox, alert_feedback
tenant_rules 1--* tenant_rule_accounts *--1 accounts
connector_connections 1--* connector_grants, connector_items, connector_quarantine, connector_oauth_states
idp_connections 1--* login_states ; local_identities 1--* local_auth_tokens, local_auth_events
run_cap_versions 1--* run_cap_events ; prepulled_intents 1--* prepulled_intent_events ; work_units 1--* work_unit_events
eval_runs 1--* eval_case_results, eval_metrics, eval_baselines ; dsar_requests 1--* dsar_events ; retention_runs 1--* retention_actions
crm_writes 1--* crm_write_events ; crm_mappings *--1 accounts
```

`chunks` (vector) carries `tenant_id, account_id, doc_id` but no declared FKs (partitioned table; relation is by convention). `tool_calls` is keyed by run/account columns without hard FKs to `account_runs` (it was created in 0001 before runs), unverified in detail.

### 2.4 Row-Level Security

* Roles (cluster-global, created idempotently in migrations): `app_rw` (LOGIN, NOINHERIT, NOBYPASSRLS; the API and workers), `public_reader` (NOLOGIN; reached by `SET LOCAL ROLE`; may see only `facts` with `visibility='public'`; no grant on `chunks`: "two-output rule"), plus NOLOGIN, NOBYPASSRLS, NOINHERIT definer owners for cross-tenant functions: `governor_definer`, `identity_resolver`, `identity_authenticator`, `sweeper_definer`, `tombstone_writer`, `operator_resolver`, `pilot_reader`, `tenant_provisioner`, `tenant_eraser`, `account_purger`, `account_purge_lookup`, `account_purge_runner`. Migration 0071 grants `app_rw` membership to the Cloud SQL IAM users (api, worker, job) and refuses any superuser/BYPASSRLS role.
* Tenant accessor: `app_tenant()` = `NULLIF(current_setting('app.tenant_id', true),'')::uuid`. Unset => NULL => every policy matches zero rows (fail closed). Policies are written `tenant_id = (SELECT app_tenant())` so it is evaluated once per query.
* Every tenant table: `ENABLE` + `FORCE ROW LEVEL SECURITY` (applies even to the owner) with `tenant_isolation` policy `TO app_rw`, `USING` and `WITH CHECK`. `tenants` uses `id = app_tenant()`.
* **Private workspaces** (0063, generated from `config/ownership.yaml`): second layer. `app.user_id` and `app.scope` are set next to `app.tenant_id`. 102 tables are `owned` (policy adds `owner_visible(owner_user_id)` = system scope OR owner = acting user); 62 are `tenant`-level (users never read directly). `fill_owner()` trigger derives the owner of child rows from account/run/parent. So within one client, one user cannot see another user's accounts, facts, files, chats. `tests/isolation/test_ownership.py` fails if a table is in neither list.
* `tenant_txn(tenant_id, snapshot=False, user, system)` (`db/session.py`): one transaction = one tenant context; runs `set_config('app.tenant_id'|'app.user_id'|'app.scope', ..., true)` (transaction-local, so a pooled connection cannot leak tenant A into B). `snapshot=True` => `REPEATABLE READ, READ ONLY` for exporters. Acting user / system scope come from ContextVars (`set_actor`, `acting_as`, `system_scope`). With neither, owned rows are invisible. `untenanted_session()` is for definer lookups only. `lazy_pre_ping` replaces pool_pre_ping.
* Cross-tenant needs (sweeper, schedule tick, identity lookups, governor, purge) go only through **SECURITY DEFINER functions** with pinned `search_path`, identifier-only returns, `EXECUTE` to `app_rw` only, owned by the NOLOGIN roles above (0070 moved 11 sweeper lookups off the migration login role because on Cloud SQL that role could otherwise bypass or break). Each carries a "reviewed:" comment that a lint requires (`lint_migrations.py`).
* Insert-only ledgers: `GRANT SELECT, INSERT` only (no UPDATE/DELETE): `tool_calls`, `wallet_ledger`, `credit_entries`, cap/work-unit event tables, `audit_events`, etc.
* LangGraph tables: policy `starts_with(thread_id, '<tenant_uuid>:')`; the checkpointer opens a **dedicated connection** per slice with a session-level `set_config('app.tenant_id', ..., false)` and closes it afterwards (`platform/checkpoint.py`); `setup()` runs only in the migrator.
* Verification: `tests/isolation/` (12 files, matrix YAML + plant-and-probe), nightly Cloud Scheduler `isolation-check` task, `scripts/check_rls_catalog.sql`, `docker/ci/probe_isolation.py` in CI.
* Weak spots: FKs bypass RLS (hence the composite FKs, but not every table has one); the whole model rests on app code always going through `tenant_txn` (raw `session_factory()` / `untenanted_session()` exist for jobs); `tombstones`/`platform_tombstones` and the purge roles are recent (0096/0097) and lightly exercised.

### 2.5 Other stores

| Store | Use | Detail |
|---|---|---|
| LangGraph checkpoints | durable research graph state in Postgres | `CHECKPOINT_DATABASE_URL` (password-bearing URL even in cloud, from Secret Manager); a slice is ~27.5 min; resumed after worker death |
| pgvector | passage/chunk retrieval for Ask and the leak gate | see `chunks` above; two embeddings (1536 hashing, 384 bge-small) |
| Full-text | `tsvector` on `chunks` (english) and `document_passages` (simple) with GIN |
| Redis (Memorystore 7.2, TLS 6378) | (a) auth/user rate-limit token buckets, fail-closed 503 when configured but down (`identity/rate_limit.py`); (b) connector governor rate store; (c) optional index for the provider-result research cache (Postgres row is the record; failures read as a miss); (d) RAG cache. "Redis holds nothing authoritative or private" (`tenancy/catalog.py`). Local: `redis:7-alpine`. Key prefix `t:<tenant>:<purpose>:`. TLS uses `ssl_check_hostname=false` with the instance CA mounted from Secret Manager. |
| Cloud Storage | data bucket (CMEK, versioning + 7-day soft delete, uniform access, public access prevention enforced, CORS to the web origin): raw uploads under `tenants/<tenant>/files/...` (neutral keys, no file names), raw provider payloads `*.raw.json(.gz)` (deleted after `RAW_RETENTION_DAYS`), `quarantine/` (7 d), `uploads/` (3 d), incomplete multipart abort (2 d). Browsers upload straight to GCS via resumable sessions (API never sees the bytes; `UPLOAD_ORIGIN=https://storage.googleapis.com`). Separate **audit archive bucket**: 7-year retention policy (lock is a variable, default `false`, irreversible). Local: fake-gcs-server and a `payloads` volume; `devstore` mimics resumable upload in the replica stack. |
| Audit hash chain | `audit_events`: per-tenant monotonic `seq`, `prev_hash`, `hash` = SHA-256(prev + canonical fields); insert-only; `audit.chain` verifies gaps (deleted), broken links (replaced), mismatches (edited) and truncation vs highest verified seq; nightly `/tasks/audit-verify` and `audit_chain_checks` rows. |

---

## 3. Infrastructure

### 3.1 Terraform layout (`infra/`, ~4,400 lines; Terraform >= 1.9, google provider ~> 6.0)

* `infra/stack/main.tf` (856 lines): the whole environment composed from modules; `infra/envs/staging` and `infra/envs/prod` are thin roots calling it (`backend "gcs" {}`, state bucket configured at init). `staging` is applied; `prod` is prepared but not applied.
* Offline plan tests: `infra/stack/tests/plan.tftest.hcl` (+ module tests) with mock providers (`make infra-test`). No CI job ever applies; the owner applies a saved plan (`terraform plan -out`, `apply plan.tfplan`).
* Cloud Run images are `ignore_changes` so Terraform never rolls a running image back; tfvars `images` must be updated by hand afterwards (documented bookkeeping).

| Module | Resources |
|---|---|
| `project` | enables APIs (`google_project_service`), removes default service accounts |
| `iam` | 8 service accounts (api, worker, job, web, migrate, tasks, scheduler, deployer); least-privilege project roles (cloudsql.client/instanceUser, logging/monitoring/trace writers, tasks enqueuer, aiplatform.user only when `vertex_ai`); **Workload Identity Federation** pool + GitHub provider conditioned on repo + ref (`refs/heads/main`) + environment; deployer impersonation |
| `network` | VPC, /24 subnet for Cloud Run direct VPC egress, deny-all ingress firewall, private services access, Cloud Router + Cloud NAT with one reserved static IP (output `nat_ip` for provider allow-lists) |
| `secret_manager` | KMS key ring + CMEK key; secret containers (values added out of band) with per-service accessor IAM; versions pinned via `secret_versions` map |
| `cloud_sql` | Postgres 16, private IP only, `ssl_mode ENCRYPTED_ONLY`, IAM authentication flag, CMEK, PITR, backups (7 days staging / 30 prod), deletion protection, audit-ish logging flags, `max_connections` input, IAM users for api/worker/job; `ZONAL` default (REGIONAL for prod) |
| `cache` | Memorystore Redis 7.2, tier BASIC (staging) / STANDARD_HA (prod), TLS SERVER_AUTHENTICATION |
| `gcs` | data bucket + audit-archive bucket (see 2.5) |
| `tasks` | 3 Cloud Tasks queues: `runs` (5 dispatch/s, max_attempts 8, backoff 10-300 s), `documents` (2/s, 20 attempts, 10-60 s), `jobs` (5/s, 5 attempts) |
| `registry` | Artifact Registry `ai-staging-images` (docker) |
| `identity_platform` | Google Identity Platform config (authorized domain; provides JWKS/issuer/audience for tenant tokens) |
| `cloud_run` | services: **api** (1 vCPU, 2 GiB, concurrency 40, timeout 3600 s, ingress internal-only, min 0 / max 3 staging), **worker** (4 vCPU, 12 GiB, concurrency 4, gen2, CPU always-on, optional NVIDIA L4 GPU via `worker_gpu`, GCS bucket mounted at `/mnt/blobs`, **clamd ClamAV sidecar** 1 vCPU/4 GiB, min 0 / max 2 staging), **web** (1 vCPU, 512 MiB, ingress internal load balancer only). Startup probe `/readyz`, liveness `/healthz`. Direct VPC egress ALL_TRAFFIC (through NAT). Secrets via `secret_key_ref`; Redis CA mounted as a file. `public_invoker` for api and web (Cloud Run IAM allUsers, but network ingress restricted); worker invokable only by the tasks SA |
| `cloud_run_jobs` | jobs `migrate` (api image, `alembic upgrade head`, secrets `migrator-database-url`, `app-rw-password`, 1800 s), `spend-invariants` (worker image, nightly), `reingest-tables` (manual). (Memory notes an `ai-staging-purge` job exists in staging but it is **not in Terraform**: unverified drift, created by hand.) |
| `scheduler` | Cloud Scheduler HTTP ticks to the worker (OIDC, UTC) and one job runner, table below |
| `edge` | Global external HTTPS load balancer, static IP, Google-managed certificate, TLS 1.2+ MODERN policy, HTTP -> HTTPS redirect, serverless NEG to `web`; **Cloud Armor**: OWASP CRS 3.3 sets at sensitivity 1 (one opt-out for rule 921150), cve-canary rule, rate_based_ban on sign-in/verify/reset/invite/SSO-start paths (30/min, 10-min ban), same on operator login routes, general per-client limit (default 1200/min), `_next/static` allow |
| `observability` | log bucket, log-based metrics (rls_denied, scheduler_failed, events), uptime checks, ~8 hand-written alert policies (uptime, 5xx, p95 latency, task backlog, SQL, migration failed, scheduler failed, event) + **26 generated alert policies** + dashboard from `config/telemetry/*.yaml` via `scripts/gen_observability.py`; application-metric alerts off by default (`workload_alerts_enabled=false`, waiting on OTLP exporter, "V-OBS-1") |
| `budget` | Cloud Billing budget with alerts at 50 %, 90 %, 100 % (two 100 % rules) |

Other: KMS BYOK key `*-byok` (90-day rotation, `prevent_destroy`) with encrypt/decrypt only for the api SA; custom IAM role to create/read `tenant-*` secrets (conditioned to that prefix) for api + worker.

Environment differences (code defaults): staging scaling `api/web 0-3, worker 0-2`, Redis BASIC, DB ZONAL max_connections 200, uptime period 300 s, `research_caps` default **"off"** (owner decision: all spend caps off, temporary); prod scaling `1-10 / 1-10 / 1-8`, REGIONAL DB 400 connections, STANDARD_HA, 60 s uptime. Real staging DB tier lives in the git-ignored tfvars (example `db-custom-1-3840` = 1 vCPU, ~3.75 GB; an old memory note says staging DB saturates): unverified exact value.

### 3.2 Cloud Scheduler ticks (UTC)

| Name | Cron | Target | Paused? |
|---|---|---|---|
| schedules-tick | `*/15 * * * *` | `/tasks/schedules-tick` (start due scheduled refreshes) | no |
| sweep | `*/5 * * * *` | `/tasks/sweep` (orphaned runs/files) | no |
| notify-relay | `*/5 * * * *` | `/tasks/notify-relay` (outbox emails) | no |
| metrics | `*/2 * * * *` | `/tasks/metrics` | no |
| maildrive-sync | `5 * * * *` | `/tasks/maildrive-sync` | no |
| isolation-check | `0 2 * * *` | nightly cross-tenant probe | no |
| access-expiry | `30 3 * * *` | warn/expire idle users | no |
| audit-verify | `15 4 * * *` | recompute hash chains | no |
| retention | `45 3 * * *` | data retention | **paused** |
| alert-digest | `0 7 * * *` | digest emails | **paused** |
| spend-invariants (Cloud Run job) | `17 3 * * *` | spend consistency check | no |

### 3.3 Secrets (Secret Manager containers; values never in Terraform/images)

`checkpoint-database-url`, `migrator-database-url`, `app-rw-password`, `anthropic-api-key`, `moonshot-api-key`, `csrf-signing-key`, `operator-google-client-secret`, `p-search-api-key`, `p-scrape-api-key`, `p-signals-api-key`, `p-signals-api-token`, `p-people-api-key`, `p-org-api-key`, `smtp-password`, optional `maildrive-google-client-secret`, `maildrive-microsoft-client-secret`, `redis-server-ca` (public CA, written by Terraform), plus runtime-created `tenant-*` secrets. Names only here. (Local `.env` exists, mode 600; contents not read.)

### 3.4 Docker and compose

| File | Notes |
|---|---|
| `docker/api.Dockerfile` | multi-stage `python:3.12-slim@sha256`, uv sync frozen, compiles the TypeScript Word renderer and npm render tools, bakes bge-small ONNX model, installs nodejs + poppler + LibreOffice, non-root uid 10001, HEALTHCHECK on `/healthz`, uvicorn single worker with `--proxy-headers --no-access-log`; also the migrate image (copies `migrations/` + `alembic.ini`) |
| `docker/worker.Dockerfile` | same base; `ARG UV_GROUP=cpu|gpu` (GPU: onnxruntime-gpu + CUDA 13 libs, `LD_LIBRARY_PATH`); `ENTRYPOINT python -m account_intelligence.worker` (CLI commands: serve, spend-invariants, reingest-tables, ...), overridden to uvicorn in Cloud Run |
| `web/Dockerfile` | node:22-slim@sha256, Next standalone, non-root, npm removed, source maps deleted |
| `docker/ci/*` | `image_checks.sh` (non-root, forbidden files), `hermetic_stack.sh`/`hermetic_run.py` (CI stack with fake model), `probe_isolation.py`, `sign_images.sh` (cosign keyless), `smoke_deployed.sh` |
| `compose.yml` | hermetic local stack: `db` (pgvector pg16), `gcs` (fake-gcs-server), `tasks` (Cloud Tasks emulator), `redis`, `migrate`, `worker` (:8081, `ALLOW_UNAUTHENTICATED_TASKS=1`), `fake-idp` (:9090, `ops/dev/fake_idp.py`), `api` (:8080), `web` (:3000); source mounted read-only; hard-coded dev passwords (`dev`, `local-dev-only`) local only; `RESEARCH_CAPS=off`; fixed subnet 10.213.0.0/24 |
| `compose.live.yml` | opt-in override: injects `.env` real keys into api + worker, `CONNECTORS=on`, `DEV_UNCAPPED=1`, `DEV_ASSUME_PROVIDER_BALANCE`, `DEV_AUTO_APPROVE_CAPS` => **costs real money**; the memory file says never start paid runs unasked |
| `compose.replica.yml` | separate project `abm-replica`: db :5433, redis :6380, api :8090, web :3010, `objectstore` (devstore resumable upload server :8092), `LLM_FAKE=hermetic`, `CONNECTORS=hermetic`, `RESEARCH_ENGINE=graph` |

### 3.5 CI/CD (`.github/workflows`, 783 lines)

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | push + pull_request | jobs: `lint-ci` (actionlint, shellcheck), `quality` (uv sync, env registry check, name scan, `make lint static contract` + more), `infra` (fmt, validate, offline tests), `web` (typecheck, lint, vitest, build), an e2e job (Playwright), `dependency-audit` (uv + `npm audit --audit-level=high`), `secret-scan` (gitleaks), `images` (build api/worker/web, inspect, upload artifacts), `hermetic` (stack of just-built images, one run end to end, isolation probe). Actions pinned to commit SHAs. |
| `deploy-staging.yml` -> `_deploy.yml` | push to `main` (after green CI) or dispatch | push images, cosign sign, run migrate job from new api image, deploy by digest, smoke, roll back on failure; authenticates by Workload Identity Federation (no stored keys) |
| `deploy-prod.yml` | manual dispatch, `production` environment | promotes the same digests |
| `eval.yml` | nightly cron `17 2 * * *`, dispatch, on prompt/routing change | model evals incl. live tabular eval (calls the paid model) |
| `CODEOWNERS` | | only credential/auth files, all owned by @Sakib-11 |

Caveat: whether these workflows actually run green on GitHub (secrets/WIF configured, Actions enabled) is **unverified**. Auto-memory says the real recent releases used "one Cloud Build + gcloud" (the section 2.2 "emergency path"), and the local branch is 9 commits ahead of `origin/main` and 2 ahead of `origin/feat/final-product`. In practice the "emergency" manual path appears to be the routine one. Image tags in use look like `rel-<shortsha>`.

### 3.6 Release / deploy process (as practiced)

1. Build api + worker on Cloud Build (laptop uv download stalls), `gcloud builds submit --tag` for web.
2. `make release-check` (Trivy HIGH/CRITICAL fixable, image inspection).
3. `gcloud run jobs update/execute ai-staging-migrate` (migrations first), then services worker -> api -> web by digest.
4. Smoke (`docker/ci/smoke_deployed.sh`), then update `terraform.tfvars` digests. Rollback = traffic to previous Cloud Run revision.
5. Infra changes: `terraform plan -out` then apply in `infra/envs/staging` with `GOOGLE_BILLING_PROJECT`/`USER_PROJECT_OVERRIDE`; never apply a stale plan (one did roll api/worker back).

### 3.7 Environment variables

Source of truth: `src/account_intelligence/config.py` (`Settings`, ~170 fields, 712 lines; each has purpose, secret flag, required_in/forbidden_in per profile, component). `docs/ENVIRONMENT.md` is generated from it (`scripts/gen_env_docs.py`); a test fails if code reads an unregistered env var. `python -m account_intelligence.config` / `make env-check` validates and lists problems by name only. Profiles: `dev`, `test`, `staging`, `production`; cloud profiles refuse local switches (`ALLOW_UNAUTHENTICATED_TASKS`, `PAYLOAD_DIR`, `LLM_FAKE`, plain `ANTHROPIC_API_KEY`) and require Cloud SQL IAM triplet.

Names by group (no values):
* Core: `APP_ENV SERVICE_ROLE LOG_LEVEL PORT K_SERVICE CLOUD_RUN_JOB CONFIG_DIR PUBLIC_BASE_URL`
* Database: `DATABASE_URL CHECKPOINT_DATABASE_URL MIGRATOR_DATABASE_URL APP_RW_PASSWORD APP_RW_IAM_USERS DB_POOL_SIZE DB_MAX_OVERFLOW CLOUD_SQL_INSTANCE DB_IAM_USER DB_NAME`
* Identity: `JWKS_URL JWT_AUDIENCE JWT_ISSUER STAFF_JWT_ISSUER STAFF_JWT_AUDIENCE STAFF_JWKS_URL STAFF_WORKSPACE_DOMAIN STAFF_OPERATOR_EMAILS STAFF_SUPPORT_EMAILS STAFF_EXTERNAL_EMAILS STAFF_IDP_ASSERTS_ROLE CSRF_KEY_SECRET`
* GCP: `GCP_PROJECT GCP_REGION TASKS_QUEUE TASKS_QUEUE_DOCUMENTS TASKS_QUEUE_JOBS WORKER_URL TASKS_SERVICE_ACCOUNT TASKS_AUDIENCE ALLOW_UNAUTHENTICATED_TASKS PAYLOAD_BUCKET PAYLOAD_DIR BLOB_MOUNT_DIR REDIS_URL BYOK_KMS_KEY METRICS_EXPORT`
* Models: `LLM_ACCESS (anthropic_api|vertex_ai) VERTEX_REGION MODEL_ROUTER ANTHROPIC_SECRET ANTHROPIC_API_KEY MOONSHOT_SECRET MOONSHOT_API_KEY(_FILE) MOONSHOT_BASE_URL MOONSHOT_API_DIALECT MOONSHOT_MODEL XAI_BASE_URL XAI_SECRET XAI_API_KEY(_FILE) EVAL_MODEL EVAL_EFFORT LLM_FAKE FAKE_MODEL_SCENARIO`
* Providers: `P_SEARCH_API_KEY P_SCRAPE_API_KEY P_SIGNALS_API_KEY P_SIGNALS_API_TOKEN P_PEOPLE_API_KEY P_ORG_API_KEY CONNECTORS (on|off|hermetic)`
* Research/spend: `RUN_BUDGET_USD RUN_TOOL_CALL_CAP RUN_RESERVE_CREDITS RESEARCH_CAPS (on|off) RESEARCH_CACHE RESEARCH_ENGINE (graph|hermetic|skill) DEV_ASSUME_PROVIDER_BALANCE DEV_AUTO_APPROVE_CAPS DEV_UNCAPPED RUN_SWEEPER ORPHAN_AFTER_S SWEEP_INTERVAL_S`
* Ask: `ASK_WHOLE_FILE_MAX_CHARS ASK_TABLE_IN_CONTEXT_MAX_CHARS ASK_WEB ASK_WEB_SEARCHES_MAX ASK_WEB_READS_MAX ASK_WEB_RESULTS_MAX ASK_ROUTE_LLM_FALLBACK`
* Documents/scan/embedding: `DOCUMENT_SCANNER (signature|clamd) CLAMD_HOST CLAMD_PORT DOCUMENT_SEMANTIC_EMBEDDER FASTEMBED_CACHE_PATH AUDIO_TRANSCRIPTION`
* Mail/OAuth: `SMTP_HOST SMTP_PORT SMTP_USERNAME SMTP_PASSWORD MAIL_FROM MAIL_FROM_NAME MAILDRIVE_GOOGLE_CLIENT_ID/_SECRET(_FILE) MAILDRIVE_MICROSOFT_CLIENT_ID/_SECRET(_FILE) GMAIL_API_BASE GOOGLE_DRIVE_API_BASE MICROSOFT_GRAPH_API_BASE`
* Rendering/parity: `EXPORT_RENDERER EXPORT_RENDER_CHECKS PARITY_BUNDLE PARITY_DOCX_CLI SCREENS NEXT_DIST_DIR PARITY_WEB_ORIGIN`
* Dev object store: `DEVSTORE_PUBLIC_URL DEVSTORE_ROOT DEVSTORE_ALLOW_ORIGIN DEVSTORE_SESSION_TTL_S`
* Web (separate): `API_ORIGIN UPLOAD_ORIGIN OPERATOR_GOOGLE_CLIENT_ID/_SECRET/_REDIRECT_URI STAFF_WORKSPACE_DOMAIN STAFF_EXTERNAL_EMAILS`

### 3.8 Hermetic / fake mode and local dev flow

* **Hermetic model**: `LLM_FAKE=hermetic` uses `platform/fake_model.py` with scenario JSON in `config/hermetic/fake_model/` (happy, budget_exhausted, judge_disagrees, prompt_injection, provider_429_then_ok, provider_5xx_exhausted). Forbidden in cloud profiles.
* **Hermetic providers**: `CONNECTORS=hermetic` serves cassettes `config/hermetic/providers/*.json` through `httpx.MockTransport` with the account's domain substituted; host resolution returns a fixed public IP; placeholder keys. `app.container` refuses it outside dev/test. `RESEARCH_ENGINE=hermetic` for no-outside-call research.
* **Fake IdP**: `ops/dev/fake_idp.py` issues OIDC tokens locally; dev token via test user.
* Flow: `cp .env.example .env` -> `make up` (hermetic stack builds + migrates) -> `make seed` (`scripts/seed_pilot.py`) -> web at :3000 (local login `test@local.dev` per memory), or `make replica-up` for the replica profile. Native dev: `make up-deps`, `make run`, `next dev` on :3000 (the API accepts only origin localhost:3000). Live runs only through `compose.live.yml` and cost money.
* Tests use a scratch DB (`TEST_DB`, `PGPASSWORD=dev`, `APP_RW_PASSWORD`); see section 6.

---

## 4. Security posture

### 4.1 Controls present (verified in code)

| Area | Implementation |
|---|---|
| Tenant sign-in | OIDC (`identity/oidc.py`, JWT verified via JWKS), SAML (`identity/saml.py`, signxml, assertion-id replay table), or first-party password (Argon2id t=3, 64 MiB, p=2, 12-128 chars, common-password list, dummy-verify for unknown email, uniform 401). Registration by invite only (self-signup off). Tenant is never a request field: derived from session/token. |
| Sessions | 256-bit random token in `__Host-ai_session` HttpOnly Secure SameSite=Lax cookie; DB stores SHA-256; idle 30 min, absolute 8 h, rotation on sign-in/privilege change, revoke-all; admin requires MFA amr claim and step-up window 10 min (all marked `status: proposal`, unmeasured) |
| CSRF | HMAC(session id) under a >=32-byte server key, header `X-CSRF-Token`, required for every unsafe method on cookie-authenticated calls (constant-time compare) |
| Authorization | default-deny **route catalog** (`api/policy.py`): every `/v1` and `/operator/v1` route needs an entry (roles, principal types, audit class, tenant source) else 403; `scripts/check_route_catalog.py` in CI; roles in `config/roles.yaml` |
| Operator plane | separate: Google Workspace OIDC, email allowlists, MFA, no tenant principals accepted; support impersonation via time-boxed `support_grants` with audit |
| Rate limits | Redis token buckets per IP/account for auth (fail closed 503 if Redis configured and failing), progressive lockout (5/1 min, 8/5 min, 12/1 h), per-user action buckets in `config/rate_limits.yaml` (upload, export, download-everything, ...), Cloud Armor edge limits |
| Web edge | per-request CSP nonce in `web/proxy.ts` (`script-src 'self' 'nonce-...' 'strict-dynamic'`, `style-src 'self' 'nonce-...'`, `frame-ancestors 'none'`, `connect-src 'self'` + upload origin), HSTS, BFF forwards only `__Host-` cookies and an allowlist of headers; API responses carry nosniff, X-Frame-Options DENY, CSP `default-src 'none'`, no-referrer |
| SSRF (`connectors/security.py`) | https only, port 443 only, no userinfo/whitespace, refuses `localhost`, `.internal`, metadata hosts/IPs (169.254.169.254, fd00:ec2::254, 100.100.100.200), IP literals must be globally routable, hostnames resolved and every address must be public; re-checked on redirect targets. Design note: connectors never fetch pages themselves, **the provider does**, so the check is defensive for "do not ask a provider to read an internal address". IdP client pins connections (`identity.ports.PinnedBackend`). |
| Upload pipeline | direct-to-GCS resumable upload; ClamAV `clamd INSTREAM` in a worker sidecar (cloud: only `clamd` accepted, otherwise every upload is rejected, i.e. fail closed); a structural signature scanner for PDF `/JS /Launch /EmbeddedFile`, Office macros, executables inside archives; archive guards (depth 2, 2000 members, ratio 200, 20 GiB); parsers run in a **subprocess sandbox**: empty env, isolated Python, rlimits, audit hook refusing network/process/writes, wall-clock timeout (`documents/sandbox.py`); `run_python` Ask tool runs in the same lockdown behind a per-tenant flag |
| Prompt-injection/output safety | `safety/prefilter.py`, `safety/client_output.py`, `config/client_output_denylist.txt` (provider names/credit counts/costs are operator-only), leak checks (`leak_checks`, `documents/leakcheck.py`), blind verifier downgrade-only (ADR-0043) |
| Secrets | Secret Manager + CMEK, no secrets in images/tfvars/state, `SecretStr` in settings, error messages list names only, gitleaks pre-commit + CI, tenant model keys KMS-envelope encrypted, API keys never reach the browser bundle |
| Audit | insert-only hash-chained per-tenant audit log + nightly verify + 7-year locked-optional archive bucket; audit of reads (ADR-0059) |
| Crypto/transport | Cloud SQL ENCRYPTED_ONLY, CMEK for SQL/GCS/secrets, Redis TLS, TLS 1.2+ at LB |
| Supply chain | digest-pinned base images, GitHub Actions pinned to SHAs, cosign keyless signing, Trivy fixable HIGH/CRITICAL gate, `npm audit`, lockfiles, `--ignore-scripts` on npm ci |

### 4.2 Weaknesses and risks I noticed

| # | Severity (my judgement) | Finding |
|---|---|---|
| 1 | High (doc/assurance) | **Stub security packages always report "all clear"**: `src/security/pentest_gate.beta_gate()` returns `True`; `rotation.overdue()` returns `[]`; `secrets_scan.scan()` returns `[]`; `soc2.coverage()` returns `1.0`. Tests (`tests/crosscut/test_x5_security.py`) pass against them. The tables behind them (`pentest_tracker`, `soc2_evidence`, `rotation_events`) exist but nothing computes real results. Anyone reading a green "security gate" is misled. Real secret scanning is gitleaks, not this module. |
| 2 | Medium | Other stubs: `metrics/*` (`check_all`, `generate` are `pass`), `registers/*` (claims, decisions, messaging, risks: `pass`), `unitcost/*` (all aggregators `pass`; the `unit_cost_rollups` table has no writer here). They are imported only by crosscut tests. Marked "unimplemented" in docstrings, but `security` is not. Dead weight in wheel + coverage + importlinter roots. |
| 3 | High | **Real-account data in git history**: `unwanted-things/real-account-fixtures/tests/fixtures/gsk/GSK_workspace.zip` is a *tracked* file (real company workspace with raw tool results), moved, not purged, and `main`/`feat/final-product` exist on a GitHub remote. If the remote is public or shared, that is a leak; history rewrite needed to remove. Also `.gitignore` says `*.zip` but the file is tracked. Whether the repo is private: unverified. |
| 4 | Medium | The parser/`run_python` "sandbox" is a Python subprocess with `-I`, rlimits and a `sys.addaudithook`. Audit hooks are a speed bump, not a security boundary (ctypes, native extensions such as pyarrow/pillow/lxml parsing hostile files, and hook removal tricks). Real isolation would need gVisor/nsjail/separate Cloud Run job without VPC/secrets. In Cloud Run the worker with DB credentials, GCS mount and provider keys runs the parser child in the same container (child gets an empty env, but same filesystem/network namespace, only blocked by the hook). |
| 5 | Medium | Worker, API and the Cloud SQL IAM identity share one broad `app_rw` role; compromise of any code path that skips `tenant_txn` (raw `session_factory()`, `untenanted_session()`, definer functions) defeats isolation. Isolation relies on convention + test matrix; owned/system scope can see all of a tenant's users. System scope (`set_system`) is a context var any code can set. |
| 6 | Medium | `CHECKPOINT_DATABASE_URL` is a password-bearing URL in Secret Manager for the LangGraph checkpointer even in cloud (rest uses IAM auth). Plus `migrator-database-url` and `app-rw-password`. Secret rotation is a manual process; `security/rotation.py` is a stub; `config/secrets_rotation.yaml` is data only. |
| 7 | Medium | Cloud Run `allUsers` invoker on api and web (ingress restricted by `INTERNAL_ONLY` / internal LB). Correct only while ingress settings hold; a mistaken ingress change would expose the API publicly. The web service reaches api by its `run.app` URL over VPC egress; trust relies on ingress settings only (no IAP, no mTLS). |
| 8 | Medium | Redis TLS uses `ssl_check_hostname=false` (CA pinned, hostname not checked). Redis BASIC tier on staging (no HA). Auth rate-limit denies sign-in when Redis is down (availability trade). |
| 9 | Medium | Session/MFA policy values are explicitly "proposal" and unmeasured (`config/session.yaml`, `rate_limits.yaml`, `research_files.yaml` etc. say `status: proposal`). No local MFA/TOTP; admin password login refused, so admins need SSO. No refresh-token rotation work. SMTP unset => verification/reset mail refused (fail closed) until configured. |
| 10 | Medium | Cloud Armor rate limit default 1200/min per client and the sign-in limit is IP based; a shared NAT (office) can hit it. OWASP CRS at sensitivity 1 only. |
| 11 | Low-Med | CSP `style-src 'nonce-'` is strict, but dev allows `'unsafe-eval'`. `FORWARDED_ALLOW_IPS` set to a subnet in compose; in cloud the api trusts proxy headers only from loopback so per-IP auth limiting depends on Cloud Armor. |
| 12 | Low-Med | `compose.yml` and tests embed fixed dev credentials (`dev`, `local-dev-only`) on published ports 5432/6379/4443/8123 bound to all interfaces by default (`ports: ["5432:5432"]`). Fine on a laptop, risky on a shared network. |
| 13 | Low | Prod not applied; `archive_lock=false` => audit archive retention not yet immutable. `workload_alerts_enabled=false` => no alerts on app metrics (SLO alerts missing), only infra and log-event alerts. `retention` and `alert-digest` ticks are paused in staging (retention enforcement is off). |
| 14 | Low | Stale local files: `infra/envs/staging/staging.plan` (a Terraform plan file may embed sensitive values) is on disk, git-ignored; `.terraform` provider caches 480 MB. `.env` mode 600 holds real keys; never in git (`.env*` ignored, `.env.example` tracked). |
| 15 | Low | Heavy image attack surface: LibreOffice + Node + poppler in both Python images; `apt-get upgrade` unpinned. |
| 16 | Info | `ASK_WEB` web lookups are provider-mediated (`p_search`), so SSRF is the provider's problem; web is OFF until secret/grant is wired (memory note). Tools on every Ask route (`run_python` flag per tenant) widen the attack surface for prompt injection from uploaded files. |

### 4.3 Stub packages (verified)

`src/security` (4 modules, 27 lines), `src/metrics` (13), `src/registers` (25), `src/unitcost` (33): total ~100 lines of no-ops. Referenced from `tests/crosscut/test_x5..x8*.py`. Known problem #14 in `docs/guide/06-known-problems.md`.

---

## 5. External providers and APIs

### 5.1 Data providers (connector layer `src/account_intelligence/connectors/providers/`, config `config/providers.yaml`, cassettes `config/hermetic/providers`)

Code names are deliberately opaque (`p_*`); the config's `display_name` is "internal only; never shown in client material" (client files say "6th agents").

| Code | Real vendor (from `display_name`/`base_url`) | Operations | Used for | Cost model in config | Cache TTL (`research_cache.yaml`) |
|---|---|---|---|---|---|
| `p_search` | **Parallel** (parallel.ai) | `web_search`, `read_page` (Extract) | open web search, read known pages; also Ask's web lookups (key mounted on the api since 2026-11-02) | USD per call by mode (turbo/fast 0.001, basic/advanced 0.005, +0.001/extra result beyond 10); read_page $0.001/URL; 600 req/min | 7 days both |
| `p_scrape` | **Firecrawl** (api.firecrawl.dev) | `scrape_job_page` | scraping careers/job-board pages | 1 credit/page (JSON extraction +4 and search-with-scrape refused); $/credit unknown (plan-dependent, `[VERIFY]`); rate 1 req/s placeholder | 7 days |
| `p_signals` | **PredictLeads** (predictleads.com/api/v3) | `news_events`, `job_openings`, `tech_detections` | news, 12-month job-posting feed, technology detections | 1 request per paged GET against a monthly plan quota; page limit 1000; $ unknown; rate placeholder 1/s | news 3 d, jobs 3 d, tech 14 d |
| `p_people` | **BitScale** (api.bitscale.ai) | `people_by_title`, `profile_enrichment` | contacts by title, profile enrichment (LinkedIn-like) | unknown credits (no visible cost on current plan, "estimate on a sample first"); 5 req/s | people_by_title 14 d |
| `p_org` | **Sumble** (api.sumble.com) | `org_resolve`, `org_tech_stack`, `people_by_technology`, `org_teams` | organisation match, tech stack with teams, people by technology | provider-reported credits (org_resolve 1 base + 1 per attribute; tech stack 1 credit per technology; people 1/person + attrs); 10 req/s; timeouts up to 70 s | org_resolve 30 d, org_tech_stack 30 d, people_by_technology 14 d |

Cross-cutting: routing table in `providers.yaml` (`routes:` need -> first/fallback); default run budget $5 / 60 tool calls (overridden when `RESEARCH_CAPS=off`); retry via LangGraph policy (3 attempts, backoff 1-30 s); circuit breaker (3 failures / 120 s, 60 s cooldown); rate limiter with cooldown honouring Retry-After; pagination cap 20 pages / 2000 items (lifted when caps off); unknown cost never treated as free (reserve $0.25); per-call ledger row with `cost_source`; adaptive per-provider concurrency governor across tenants (Postgres). Default cache TTL 7 days, tenant scoped, `run_input {"fresh": true}` bypasses; model replies cached 7 days; Redis index TTL 604800 s. Provider docs snapshots under `docs/providers/sources/`.

### 5.2 LLMs (`src/llm`, `config/models.yaml`, `chat_models.yaml`, `model_routing.yaml`)

| Model (allowlist) | Provider path | Price per MTok (in/out), from config | Use |
|---|---|---|---|
| `claude-opus-5-5` | Anthropic API (or Vertex AI via `LLM_ACCESS=vertex_ai`) | 4 / 20 | collection: planning, extraction, classification (medium effort; verifier fixed) |
| `claude-fable-5-1` | Anthropic | 10 / 50 | synthesis, narrative, verification/judging (judge must differ from extractor) |
| `claude-sonnet-5-5` | Anthropic | 2 / 10 | default chat/Ask model; the "cost_saving" arm that routes extraction to Sonnet was reverted by owner |
| `claude-haiku-5-5` | Anthropic | 0.10 / 0.50 | Ask route classifier only |
| `kimi-k3` | Moonshot AI API or Ollama Cloud (`MOONSHOT_API_DIALECT`), off unless `moonshot_enabled` | per `chat_models.yaml` | user-selectable/eval model; Ask tool harness is intended for Kimi K3 (memory) |
| `grok-4.5` | xAI API | per config | selectable chat model |

Hard allowlist; callers cannot override routing; active arm = `automatic`; prompt-cache aware cost accounting; adapters in `src/llm/adapters` (`anthropic_api`, `moonshot_api`, `xai_api`, `fake`). Tenant can bring its own key (BYOK, KMS-encrypted). Also: local embedding model bge-small (no API), Identity Platform (Google) for tenant tokens, Google Workspace OIDC for staff, Gmail / Google Drive / Microsoft Graph for per-user mail and drive sync (OAuth clients optional), SMTP for transactional mail, Cloud Monitoring/Trace, Slack/CRM: CRM write-back code exists (`crm_*`) - which CRM actually wired: unverified.

---

## 6. Tests

### 6.1 Layout and counts

* `tests/`: **501 test files, ~4,260 test functions, ~119.6k lines** (about 0.74 lines of test per line of src). Subfolders (files / lines): exports 61/20.3k, replica 60/14.6k, unit 43/4.7k, agents 37/9.2k, documents 23/6.4k, phase3 21/6.1k, people 20/5.2k, system 20/4.4k, jobs 19/4.3k, parity 19/3.1k, spend 17/5.0k, grading 17/4.0k, api 14/1.5k, connectors 13/4.4k, phase4 13/3.1k, rag 13/2.0k, isolation 12/2.2k, tracker 10/2.3k, parallel 10/2.1k, llm 9/1.8k, crosscut 9/1.3k, db 8/2.1k, workflow 6/1.4k, runs 6/1.2k, obs 5/1.0k, evals 4/0.9k, guardrails 4, research_files 2, verify 2, fuzz 1, ops 1 (backup/restore), integration 1, worker 1; `fakes/` shared fakes; `e2e/` (Playwright lives in `web/e2e`).
* Frontend: `web/tests` (vitest) + `web/e2e` (Playwright, plus `playwright.replica.config.ts`).
* Others: `evals/` (golden accounts, grading calibration, model benchmark, tabular synthetic and a GSK real-account set), `ops/load` (k6), Hypothesis property tests, OpenAPI-driven fuzz (`tests/fuzz`), mutation tests, backup/restore drill (`tests/ops/test_backup_restore.py`, `ops/dr/restore_drill.py`).
* ~178 test files are marked `requires_postgres` (need the scratch PostgreSQL with pgvector).

### 6.2 Markers and safety

| Marker | Meaning | Default |
|---|---|---|
| `eval` | golden-set agent evals | excluded by `addopts = -m 'not eval and not live'` |
| `live` | calls the **real paid model** (e.g. `tests/evals/test_tabular_live.py`) | excluded |
| `requires_postgres` | needs scratch PG | included; the session fixture drops and recreates `TEST_DB` |
| `large_file` | generates a 1 GB+ file | only with `RF_LARGE_FILE=1` |

A bare `pytest` is therefore safe by `addopts`, **but** passing your own `-m` overrides it (the last `-m` wins), and CI `eval.yml` does exactly that. **Never run `-m eval` or `-m live`** (spends money). Memory note: always `-m "not eval and not live"`.

### 6.3 How to run safely

1. `make up-deps` (db, gcs, tasks, redis) or an own scratch Postgres.
2. Use a private scratch DB per session: the session fixture **drops and recreates** `TEST_DB`, so parallel sessions on the same name destroy each other's tables. Set `TEST_DB=<your_own> PGPASSWORD=dev APP_RW_PASSWORD=local-dev-only` and the `MIGRATOR_DATABASE_URL`/`DATABASE_URL` pair (see Makefile `TEST_ENV`). Do **not** `source .env` (its `DATABASE_URL` hijacks tests).
3. Isolation first: `make test-isolation`; then `uv run --frozen pytest -m "not eval and not live"`; full pre-PR gate `make check`; `make verify` for everything incl. Terraform and builds.
4. Pure unit subsets need no DB: `pytest tests/unit tests/jobs tests/spend ... -m "not requires_postgres"` (unverified that every such folder is DB-free).
5. Hermetic end to end: `make up` + `make verify-wiring` (no keys needed); `make contract` for API/client drift.

---

## 7. Repo hygiene and size

### 7.1 Code size (lines)

| Area | Lines |
|---|---|
| `src/` Python total | **161,513** (incl. blank/comments; heavily documented) |
| - `account_intelligence/` (whole subtree) | 65,756 of which `api` 19,457, `replica` 14,498, `connectors` 6,539, `workflow` 3,804, `grading` 3,548, `rag` 2,655, `platform` 2,284, `tracker` 2,018, `chat` 1,887, `runs` 1,590, `ops` 547, `worker` 414 |
| - `exports` | 23,263 |
| - `documents` | 10,178 |
| - `agents` (ABM agent graph + prompts) | 10,100 (+253 in `account_intelligence/agents`) |
| - `parity` | 6,838 |
| - `people` 5,966; `spend` 4,514; `jobs` 3,961; `llm` 2,720; `identity` 2,385; `obs` 1,968; `tenancy` 1,585; `refresh` 1,442; `alerts` 1,115; `signals` 745; `audit` 557; `admin` 501; stubs `unitcost` 33, `security` 27, `registers` 25, `metrics` 13 |
| `tests/` Python | 119,615 (501 test files) |
| `scripts/` Python | 7,505 |
| `migrations/*.sql` | 12,644 (+ 96 wrappers) |
| `web/` TS/TSX (excl. node_modules/.next) | 24,208 |
| `render/docx` TS | 1,935 |
| Terraform (`.tf`, `.tf.json`) | 4,389 |
| `config/*.yaml` | 5,713 (about 75 files) |
| Docs | 354 markdown files in 62 `docs/` subdirs + `BUILD_LOG.md` (101 KB), 98 ADRs (`docs/decisions`, ADR-0001..0060 listed plus more) |

Tracked files: 2,931. Disk: repo 5.2 GB (mostly untracked caches: `web/node_modules` 785 MB, `.venv` 800 MB, `infra/**/.terraform` ~480 MB, `unwanted-things` 504 MB incl. 485 MB Next build output); `.git` 371 MB (large for a 1-month project: likely binary fixtures/spreadsheets committed).

### 7.2 Git history

| Fact | Value |
|---|---|
| Commits on current branch | **580**, one author (Sakib-11) |
| First commit | 2026-10-01; last 2026-11-03 (~34 days) |
| Busiest days | 2026-10-14 (112), 10-07 (93), 10-02 (67) |
| Remote | `origin` = `github.com/go6th/ABM`; remote branches: `main`, `feat/final-product`, `feat/data-layer-v1`, `feature-login`, `chore/finalize-clean-deploy`, `claude/*`, `codex/session-environment-20261018` |
| Local branches (14) | `main`, `feat/final-product` (current), `feat/replica`, `feat/research-files`, `feat/visual-pastel-3d`, `agent/backend-hardening`, `agent/ci-images`, `agent/gcp-infra`, `agent/web-hardening`, `backup/2026-10-13-main-folder`, `backup/2026-10-19-before-repair`, `claude/angry-banach-78a658` (worktree), `codex/session-environment-20261018` |
| Ahead of remote | 2 commits ahead of `origin/feat/final-product`, 9 ahead of `origin/main` (last origin/main commit 2026-10-31) |
| Working tree (snapshot) | modified `unwanted-things/README.md`, `cleanup.sh`; untracked `src/account_intelligence/ops/run_report.py`, `unwanted-things/backup-2026-11-01-before-reset/`, `docs/project-audit/` (this audit). Memory lists many other uncommitted items from earlier sessions; some may now be committed (latest commits d3831549, 6ee2ebe8, 576be403). |
| Tags | 8 |

Development style: AI-assisted ("vibe-coded") with agent branches (`agent/*`, `claude/*`, `codex/*`), extremely extensive docs/comments, many "owner decision YYYY-MM-DD" annotations, and per-feature `GUARANTEE(test_...)` markers. Single human committer; no PR review trace.

### 7.3 `unwanted-things/`

"Move, never delete" parking lot (owner rule 2026-10-16). Inventory in its README: skipped placeholder tests, an old `ProviderKey.tsx`, old `.next-*` build dirs (485 MB), design gallery stubs, a v1.11 skill zip, 43 Claude plugin checkouts (16 MB), real-account fixtures (GSK workspace zip + tests + shots; **tracked in git**), docx scratch scripts, `backup-2026-11-01-before-reset/` (untracked), the former purge-WIP now merged into live code. `cleanup.sh` holds the irreversible steps (not run). README claims: every `src/` module is imported somewhere, vulture clean at 80 %, every script referenced.

### 7.4 Other hygiene notes

* `.claude/` holds two skills (`account-intelligence-platform`: PRD, selftests, `lint_migrations.py`, `validate_assets.py`, `check_rls_catalog.sql`; and `production-deployment-consolidator`) that `scripts/check.sh` and pre-commit **depend on**: they are build tooling, not just notes. `.agents/` holds the owner's ABM skill starter code (excluded from lint). `config/skill/` + `config/skill/archive` hold the research skill package verbatim.
* Two product vocabularies coexist ("platform"/"6th agents"/"ABM"/"replica"); package `replica` is "the live product" per memory though named for a prior build.
* `docs/project-audit/` is a new untracked folder from this audit process.

---

## 8. Doc versus code drift found

| Doc | Claim | Code reality |
|---|---|---|
| `scripts/deploy_staging.sh` / `make deploy-staging` | region `asia-south1`, single `account-intelligence` image, `gcloud run deploy` of two services, references `docs/runbooks/deploy.md`, `bootstrap.md` | Live is `us-central1`, three images (`api`, `worker`, `web`) by digest, migrate job before rollout. Script is stale and misleading (it only prints). |
| `DEPLOYMENT.md` 2.1 | routine release = push to `main` + green CI -> `deploy-staging.yml` | Auto-memory and branch state show releases done by hand via Cloud Build + `gcloud` (the "emergency" path); unverified whether GitHub Actions deploy has ever run. |
| `docker/README.md` | cleanup commands in `/home/sakib/unused code ABM generator/cleanup.sh` | path is outside the repo and probably stale (repo has `unwanted-things/cleanup.sh`). |
| `RUNBOOK.md` | "Python 3.11+" | images and CI use 3.12; mypy needs 3.12 |
| `pyproject.toml` coverage comment | `fail_under = 94` re-measure needed | stale; unknown real coverage |
| `migrations/README.md` | landmarks list "0095 (current)" | head is 0097 (`EXPECTED_REVISION = "0097"`) |
| Memory/guide | `ai-staging-purge` Cloud Run job | not in `infra/` Terraform (hand-made, unverified) |
| `DEPLOYMENT.md` | "Cloud Tasks queues runs, documents, jobs" | matches; but queue settings comments mention older attempt counts - code is `max_attempts 8` on runs |
| `docs/guide/06-known-problems.md` | state "as of 2026-10-28"; "nothing committed" | many items since committed/deployed (10-30+, 11-02, 11-03 releases); the guide is older than the code |
| `SECURITY.md` | "Mutating cookie-authenticated requests require the session-bound CSRF token", "Provider names ... operator-only" | consistent with code read; deployed behaviour unverified |

---

## 9. Summary (10 lines)

1. A single-developer, AI-assisted multi-tenant Python 3.12 / FastAPI / LangGraph / Next.js SaaS: ~161k lines of src, ~120k lines of tests, 580 commits in 34 days (2026-10-01 to 2026-11-03).
2. Postgres 16 + pgvector is the core: ~205 tables, 96 SQL migrations (head 0097, no 0069), only 5 ORM models, everything else raw SQL via `tenant_txn`.
3. Isolation is real and layered: FORCE RLS on every tenant table keyed on `app.tenant_id`, a second per-user "private workspace" layer (`app.user_id`, 102 owned tables), NOLOGIN definer roles for cross-tenant jobs, insert-only ledgers, a nightly isolation check and a hash-chained audit log.
4. Infra is clean Terraform on GCP (Cloud Run api/worker+clamd/web, Cloud SQL private IP+IAM auth+CMEK, Memorystore Redis, 3 Cloud Tasks queues, 10 Scheduler ticks, HTTPS LB + Cloud Armor OWASP, Secret Manager/KMS, WIF for CI, budget + generated alerts); staging is live, prod is prepared but not applied, and no pipeline applies Terraform.
5. Release practice diverges from the docs: GitHub Actions gates and deploy workflows exist, but releases appear to be done by hand with Cloud Build + gcloud; `make deploy-staging` prints stale asia-south1 commands.
6. Dependencies: 40 runtime Python packages (lower bounds only, uv.lock pins); `pandas` looks unused; GPU/CPU embedder groups conflict by design; images carry LibreOffice, Node, poppler (heavy, large surface).
7. Providers: Parallel (search), Firecrawl (scrape), PredictLeads (news/jobs/tech), BitScale (people), Sumble (org/tech/people), each cached 3-30 days; models: Claude Opus/Fable/Sonnet/Haiku 5.x plus optional Kimi K3 and Grok 4.5; costs are mostly "unknown, plan-dependent" in config.
8. Security strengths: default-deny route catalog, Argon2id, hashed server sessions, CSRF HMAC, Cloud Armor, strict nonce CSP, SSRF validator, clamd fail-closed, KMS-encrypted tenant keys. Weaknesses: the `security`, `metrics`, `registers`, `unitcost` packages are no-op stubs and `security` always reports all-clear; the Python audit-hook "sandbox" is not a true boundary; real-account data (GSK zip) is tracked in git history; session/rate-limit/MFA policy values are unmeasured "proposals"; local MFA absent.
9. Tests: 501 files / ~4.3k tests; `eval` and `live` are excluded by default but any explicit `-m` overrides that; use a private scratch DB because the session fixture drops/recreates it; never run `-m eval`/`live`.
10. Biggest handover risks: stale docs vs code (guide dated 10-28, deploy script), unverified CI/deploy automation, unpushed local work (2-9 commits ahead), single-author knowledge concentrated in agent-written code, no layer contracts on the newest large packages (`replica`, `documents`), and caps currently switched off in staging (`RESEARCH_CAPS=off`) which removes the spend guardrails.
