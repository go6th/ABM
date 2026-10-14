# Phase 4C plan: production-ready on Google Cloud (no deployment)

Owner prompt of 2026-10-14 ("Go ahead: finish Phase 4B, then Phase 4C"). Repository
`/home/sakib/abm-replica`, branch `feat/replica`. Nothing here is applied or deployed: no
`terraform apply`, no `gcloud` command that creates, deploys, enables or deletes anything, no paid
model or data-provider call. Deploying happens only when the owner says "deploy" with a project id.

## 1. What exists today

### Infrastructure (Terraform, `infra/`)

| Path | What it holds today |
| --- | --- |
| `infra/stack/main.tf` | The whole environment composed from the modules; no defaults for project, region, billing account or domain. `infra/stack/tests/plan.tftest.hcl` plans it offline with mock providers. |
| `infra/envs/staging/`, `infra/envs/prod/` | Thin callers of `stack` with `terraform.tfvars.example` each. |
| `infra/modules/project` | Enabled APIs, service identities. |
| `infra/modules/network` | VPC, Cloud Run subnet, private services access, Cloud Router, Cloud NAT with a reserved address. |
| `infra/modules/cloud_run` | `google_cloud_run_v2_service` (api, web; worker as a service is to be decided, see 3). |
| `infra/modules/cloud_run_jobs` | Cloud Run jobs (migrate; DEPLOY.md describes the worker as a job: see 3). |
| `infra/modules/cloud_sql` | PostgreSQL instance, database, IAM user, CMEK. |
| `infra/modules/cache` | Memorystore Redis (`rate`). |
| `infra/modules/gcs` | One data bucket with IAM. |
| `infra/modules/tasks` | One Cloud Tasks queue (`runs`). |
| `infra/modules/scheduler` | One Cloud Scheduler job. |
| `infra/modules/secret_manager` | Secret containers and per-secret accessor bindings (values out of band). |
| `infra/modules/iam` | Service accounts, project roles, GitHub Workload Identity Federation pool and provider, deployer. |
| `infra/modules/edge` | Global address, managed certificate, HTTPS proxy, URL map, HTTP-to-HTTPS redirect, Cloud Armor policy, serverless NEG for web. |
| `infra/modules/registry` | Artifact Registry repository. |
| `infra/modules/observability` | Log bucket, `rls_denied` metric, generated alerts and dashboards (`scripts/gen_observability.py`). |
| `infra/modules/budget` | Billing budget. |
| `infra/modules/identity_platform` | Identity Platform config. |

KMS keys (`google_kms_*`) live in the modules above. Docs: `docs/DEPLOY.md`, `docs/infrastructure-plan.md`,
`docs/runbooks/` (deploy, restore, secrets, database, bootstrap, failover...), ADR-0017 to ADR-0020.

### Images and CI

- `docker/api.Dockerfile` (multi-stage, uv, non-root uid 10001, runs migrations as a job too),
  `docker/worker.Dockerfile`, `web/Dockerfile`, `.dockerignore`, `web/.dockerignore`,
  `docker/cloudbuild.yaml` (an older Cloud Build file; the repository's CI is GitHub Actions).
- `.github/workflows/ci.yml` (lint, quality, isolation first, infra, web with Playwright and axe,
  images), `deploy-staging.yml` (on main after CI, environment `staging`), `deploy-prod.yml`
  (manual, environment `production`), `eval.yml`.
- Compose: `compose.yml`, `compose.replica.yml` (hermetic replica stack), `compose.live.yml`,
  `compose.testing.yml`.

### Application platform seams

- Cloud adapters: `src/account_intelligence/platform/gcp/adapters.py` (Cloud Tasks, Cloud Storage,
  Secret Manager, Cloud SQL connector); model port `src/account_intelligence/platform/llm.py`
  (Anthropic, Kimi K3 behind `MOONSHOT_*`); fake model `platform/fake_model.py`
  (`LLM_FAKE=hermetic`, refused in the cloud by `assert_gcp_runtime`).
- Environment registry: `src/account_intelligence/config.py` (profiles dev, test, staging,
  production; `CLOUD` profiles refuse local switches,
  `GUARANTEE(test_cloud_profile_refuses_local_switches)`); generated `docs/ENVIRONMENT.md`
  (`scripts/gen_env_docs.py`); `.env.example`.
- Runtime wiring: `src/account_intelligence/app/container.py` (refuses `LLM_FAKE` outside
  dev and test; `MODEL_ROUTER=on` gate).

### Checks that exist and must stay at least as strict

| Check | Where |
| --- | --- |
| Route catalog (one policy and audit class per route; tenant only from the session) | `scripts/check_route_catalog.py`, `account_intelligence.api.policy.ROUTE_POLICIES` |
| RLS catalog (FORCE RLS and a policy on every tenant table; `app_rw` NOBYPASSRLS) | `scripts/check_rls_catalog.sql` |
| Wiring audit | `scripts/wiring_audit.py`, `scripts/verify_wiring.py`, `docs/WIRING.md` |
| Guarantees (every `GUARANTEE(...)` names a real test) | `scripts/check_guarantees.py` |
| OpenAPI snapshot and generated web client | `docs/api/openapi.json`, `scripts/export_openapi.py`, `web/lib/gen/` (`scripts/gen_web_client.py --check`) |
| Isolation suite and matrix | `tests/isolation/` (`matrix/matrix.yaml`, `test_ownership.py`, `test_rls*.py`), `scripts/gen_isolation_matrix.py` |
| Nightly isolation check | `src/account_intelligence/replica/isolation.py` (`isolation_checks` rows, console pill) |
| Loop inventory, routing governance, comments, runbooks, telemetry, conformance | `scripts/gen_loops.py` (`docs/architecture/loops.md`), `check_routing_governance.py`, `check_comments.py`, `check_runbooks.py`, `check_telemetry.py`, `check_conformance.py` |
| Migration lint | the skill's `lint_migrations.py` |
| CSP (per-request nonce, `strict-dynamic`, no inline styles) | `web/proxy.ts`, `web/next.config.ts` |
| Static guards (wheel packages, docstrings, ...) | `tests/system/test_static_guards.py` |
| Replica screens with axe at 1360 and 375 | `web/e2e/replica/screens.spec.ts`, `web/playwright.replica.config.ts`; click-through `web/scripts/phase4b-shots.mjs` |

### VERIFY items (`grep -rn "VERIFY("`)

V-FILES-1 (`src/research_files/blobstore.py`: browser-to-bucket resumable upload with CORS and
`Range`; verify on staging), V-LLM-2 (`platform/llm.py`, `app/container.py`, ADR-0019: the model
access path), V-REGION-1 and V-SQL-1 (ADR-0020, `docs/runbooks/database.md`), V-TASKS-1 and
V-JOBS-1 (ADR-0018), V-REDIS-1, V-PEOPLE-1 and the provider items (`p_search`, `p_scrape`,
`p_signals`, `p_people`, `p_org` in `connectors/providers/` and `docs/providers/CLAIMS.md`).
None is resolved by this phase; each stays tagged. Agent 3 adds the staging verification steps
for V-FILES-1, V-SQL-1, V-TASKS-1 and V-REDIS-1 to the runbook.

## 2. FIX_PROMPT Phases 5 to 7: code nobody may delete or rewrite now

FIX_PROMPT (`docs/build/FIX_PROMPT.md` in the main folder; read only) runs after this phase.
These stay, even when nothing calls them today; Agent 2 marks them "remove after Phases 5 to 7"
at most, and Agents 4 and 6 do not touch them except for security fixes:

| Phase | Item | Code that must stay |
| --- | --- | --- |
| 5.1 | Scheduler triggers | `/tasks/schedules-tick` and every `/tasks/*` route, `infra/modules/scheduler` (Agent 3 may extend it, not remove it) |
| 5.2 | Refresh probe and escalate, deltas | `src/refresh/debounce.py` (FIX_PROMPT also names `probe.py`, `escalate.py` and `records.py`, which are not in this tree: Phase 5 writes them), the `deltas` and `delta_items` tables |
| 5.3 | Alerts | `src/alerts/` (`engine.py`, `outbox`, `digest`), Slack, webhook and email adapters (NotConfigured) |
| 5.4 | Reconciler and sweeper | the run reconciler and sweeper in the worker, `SERVICE_ROLE=worker` |
| 5.5 | Tracing | `src/obs/setup.py` (`configure()`), `src/obs/exporters.py`, `src/obs/tracing.py` |
| 5.6 | Audit hash chain | `src/audit/chain.py` and its verification; the locked archive bucket is planned in Terraform (Agent 3 may add it as "applies on deploy") |
| 5.7 | Privacy jobs | `src/privacy/` (`dsar.py`, `retention.py`, `suppression.py`, `tombstones.py`, `minimise.py`), `config/dsar.yaml`, `config/deletion.yaml` |
| 5.8 | Private retrieval | `src/account_intelligence/rag/`, `HashingEmbedder`, `config/rag.yaml` |
| 5.9 | Start-up self-check | `scripts/verify_wiring.py` (it moves into start-up in Phase 5) |
| 5.10 | Per-tenant keys | `tenants.kms_key_arn`, the KMS modules |
| 5.11 | Research graph shape | `src/agents/` graph |
| 6.1 | Leftovers that contradict the replica | `/v1/runs/{id}/approve`, `/cap/*`, `/v1/chat/provider-key`, `ProviderKey.tsx`, redirect stubs: Phase 6 removes them with the catalog, OpenAPI and client; Agent 2 lists them, Agents 4 and 6 may remove only those Agent 2 proves dead and that are not on this Phase 6 list |
| 6.3 to 6.5 | Root stray files, `src/security/pentest_gate.py::beta_gate()`, `TECHNICAL_DOCUMENTATION.md` | Phase 6 owns them; this phase only lists them |
| 7 | CI, evals, deploy checklist | Agent 5 writes the workflows now; Phase 7 makes them pass on GitHub. Agent 3's RUNBOOK covers what `docs/DEPLOY_CHECKLIST.md` will list |

## 3. The team

At most five agents at once. Each one gets section 0 of the owner prompt and its own section,
word for word, plus the notes below. Lead: Opus, high effort; reviews every diff before merging.

| # | Agent | Model | Wave | Worktree and branch | Test database | Web port |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | security-audit | Opus | 1 | none (reads `/home/sakib/abm-replica`) | `ai_agent_security_test` (only if it runs a test) | none |
| 2 | dead-code-audit | Sonnet | 1 | none (reads `/home/sakib/abm-replica`) | none | none |
| 3 | gcp-infra | Opus | 1 | `/home/sakib/abm-agent-gcp-infra`, `agent/gcp-infra` | none | none |
| 5 | ci-and-images | Sonnet | 1 | `/home/sakib/abm-agent-ci-images`, `agent/ci-images` | `ai_agent_ci_images_test` | none |
| 4 | backend-hardening | Opus | 2 | `/home/sakib/abm-agent-backend-hardening`, `agent/backend-hardening` | `ai_agent_backend_hardening_test` | none |
| 6 | web-hardening | Sonnet | 2 | `/home/sakib/abm-agent-web-hardening`, `agent/web-hardening` | none | 3026 |

Worktrees are made by the lead with `git worktree add -b agent/<name> /home/sakib/abm-agent-<name>
feat/replica` (the Agent tool's worktree isolation would branch the main folder, which is another
session's). Test databases live on the replica Postgres (`PGPASSWORD=dev PGHOST=localhost
PGPORT=5433 TEST_DB=...`); the lead's own groups use `ai_lead_g1_test` to `ai_lead_g4_test`.

### File ownership

| Owner | Files |
| --- | --- |
| security-audit | `docs/deploy/SECURITY_AUDIT.md`, `docs/deploy/agents/security-audit.md` |
| dead-code-audit | `docs/deploy/DEAD_CODE.md`, `docs/deploy/agents/dead-code-audit.md` |
| gcp-infra | `infra/**`, `docs/deploy/RUNBOOK.md`, `docs/deploy/ARCHITECTURE.md`, `docs/deploy/agents/gcp-infra.md` |
| ci-and-images | `.github/workflows/**`, `docker/**`, `.dockerignore`, `web/Dockerfile`, `web/.dockerignore`, `docs/deploy/agents/ci-and-images.md` |
| backend-hardening | `src/**`, `tests/**`, new migrations from 0070 up (`migrations/0070_*.sql`, `.down.sql` and `migrations/versions/0070_*.py`), `config/**`, `pyproject.toml`, `uv.lock`, `.env.example`, `docs/api/openapi.json`, `tests/isolation/matrix/**`, `docs/deploy/agents/backend-hardening.md` |
| web-hardening | `web/**` except `web/lib/gen/**`, `web/Dockerfile`, `web/.dockerignore`; `docs/deploy/agents/web-hardening.md` |
| lead | `docs/deploy/PLAN.md`, `docs/deploy/REPORT.md`, `docs/build/**`, `docs/ARCHITECTURE.md`, `web/lib/gen/**`, `docs/ENVIRONMENT.md`, `docs/WIRING.md`, `docs/architecture/loops.md`, `Makefile`, compose files, every merge |

A change in a file an agent does not own goes in its report as "handoff: file, line, change".
Only backend-hardening adds migrations, numbered 0070 and up.

### Tools

Not installed locally: terraform, tflint, checkov, gitleaks, trufflehog, hadolint, actionlint,
vulture, pip-audit. Agents run them as throwaway tool containers (`docker run --rm` with the
worktree mounted read-only, for example `hashicorp/terraform`, `ghcr.io/terraform-linters/tflint`,
`bridgecrew/checkov`, `zricethezav/gitleaks`, `aquasec/trivy:0.75.0`, `hadolint/hadolint`,
`rhysd/actionlint`) or through `uvx` / `npx`. Never `docker compose`, never a container or
volume named `abm-*` or `abmgenerator-*`. Terraform runs with an empty `CLOUDSDK_CONFIG` and no
`GOOGLE_APPLICATION_CREDENTIALS`, so it cannot reach a real project; plan uses the mock providers
(`terraform test`) or placeholder credentials with `-refresh=false`.

### Merge order

1. `agent/ci-images`, 2. `agent/gcp-infra`, 3. `agent/backend-hardening`, 4. `agent/web-hardening`.
Audit reports (Agents 1 and 2) are committed by the lead on `feat/replica` when they report.
Wave 2 starts once Agents 1 and 2 have reported; the lead assigns every "needs fix" and "remove"
item to Agent 4 or 6 by file (section 4 of this plan is filled in then).

### Already known, for the audits

Found while finishing Phase 4B (2026-10-14), so the audits confirm or extend rather than rediscover:

- The cross-tenant sweep functions (`runs_orphaned`, `research_files_orphaned`,
  `documents_orphaned`, `document_parts_orphaned`, `runs_stalled`, ...) are SECURITY DEFINER owned
  by the migration user and executable by `app_rw`; they return identifiers only and are
  catalogued in `src/tenancy/catalog.py` (`JOBS`). Agent 1 judges the pattern (a NOLOGIN owner as
  in 0017 and 0021, or a separate role for the sweeper) and says whether it needs a fix.
- Same-origin checks in route handlers must use `web/lib/same-origin.ts` (`isSameOriginRouteRequest`,
  `publicOrigin`), never `request.url` or `nextUrl.origin` (98f444d). Agent 1 checks that no route
  handler still compares with the server's own address.
- `web/.probe.mjs` is a tracked leftover probe script (the only web lint warning). Agent 2 lists it.
- The images install the wheel: every package under `src/` must be listed in `pyproject.toml`
  (guard test in `tests/system/test_static_guards.py`), and the parser sandbox child gets only
  `CONFIG_DIR` (8c38c12). Agent 5's smoke test runs the import check of section 1.

## 4. Wave 2 assignments

From `SECURITY_AUDIT.md` (48e23c5) and `DEAD_CODE.md` (7a31b2c). Every item has exactly one owner.

### Already sent to Wave 1 agents still running

- Agent 3 (gcp-infra): M3 (Cloud Armor rule for reset, invite accept, SSO discovery), M6 (CSRF
  signing key secret), L16 (IAM database users: membership of `app_rw` only, a staging check),
  `UPLOAD_ORIGIN` on web. No mention of the removed manual deploy script or Cloud Build file.
- Agent 5 (ci-and-images): L10 (no uvicorn access log with `?token=` URLs), D11 (remove
  `docker/cloudbuild.yaml`).

### Agent 4 backend-hardening (`agent/backend-hardening`)

Security: H1 (closed by removing the older research-files path, D1: nothing may parse an upload
outside the documents sandbox afterwards), M1 (migration 0070: a NOLOGIN, NOBYPASSRLS owner for the
12 cross-tenant SECURITY DEFINER functions, with the policies it needs; a test that runs them as a
non-superuser owner and gets rows), M2 (the sandbox child refuses network at the OS level, e.g. a
seccomp filter on `socket`/`connect`, with a test), M4 (per-user rate limits on file Ask, uploads,
part retry, resume, Download everything, exports), M5 (a valid CSRF key secret id, matching Agent
3's), L7, L8, L9 (step-up on `manage_settings`; the `/cap/*` routes stay until Phase 6 but get the
same step-up), L12, L13, L14, L17. Production guards: the cloud profile refuses `LLM_FAKE`,
`CONNECTORS=hermetic`, `ALLOW_UNAUTHENTICATED_TASKS`, any `DEVSTORE_*`, the demo seed, the fake IdP,
`MOONSHOT_API_KEY` in env, and `APP_ENV` dev or test on Cloud Run (one test each). Health and
readiness for api and worker; graceful SIGTERM within Cloud Run's window; database pool sizes as
arithmetic against the Cloud SQL limit; security headers on the API. Dead code: D1 (backend half,
keeping the parsers, `checks`, `config`, `detect`, `blobstore`, the tables and
`research_files_orphaned`), D10, D12, D14, D25; S5 (the embedding calls logged into
`research_file_model_calls` record the document owner), S9 (every `except Exception` gets a reason or
narrows), S8 where the logic has a service module beside it (no new services); hard-coded values
into the environment registry. Patch and minor Python upgrades only if the suite passes.

### Agent 6 web-hardening (`agent/web-hardening`)

Security: L1 (HSTS with the other headers: X-Content-Type-Options, Referrer-Policy,
Permissions-Policy; the CSP unchanged), L2, L3 (by deleting the unused `EvidenceDrawer` under D4),
L4, L5, L6, L11, L15 (patch and minor npm fixes). Production source maps off. Dead code: D1w, D2,
D3, D4, D4b, D5, D7, D8, D9, then D26; S1 and D8b (re-enable the excluded unit tests that test live
code, fixing what they find); S7 (one API origin default in `web/lib`). Logic out of components into
`web/lib`; every screen has loading, empty and error states. `web/lib/gen/` is regenerated by the
lead after Agent 4 merges.

### Lead

`DEPLOYMENT.md:67,85` and `docs/infrastructure-plan.md:3` (D10, D11, D12 references), the three
docs that say the operator routes are "hidden in production" (D2), `docs/SCREEN_MATRIX.md` and
`docs/build/VISUAL_NOTES.md` (removed pages), `docs/WIRING.md`, `web/lib/gen/`, OpenAPI after
merging. D24 (`docs/design-review/`, 29 MB) after D7 lands.

### Left for Phases 5 to 7 or the owner

D6, D14b, D15 to D19 (FIX_PROMPT Phase 6), D22 and D23 (owner decisions), D13, D20, D21, D27, D28
(kept, with reasons in `DEAD_CODE.md`).

## 5. Final checks (lead)

Regenerate OpenAPI, the web client, the loop inventory, `docs/ENVIRONMENT.md` and `docs/WIRING.md`;
the full Python suite in four groups; web typecheck, lint, unit tests and production build; route
catalog, RLS catalog, guarantees, migration lint; terraform fmt, validate and plan; docker builds
and image checks; dependency audit and secret scan; Playwright with axe at 1360 and 375; restart
the replica stack and repeat the Phase 4B click-through; remove the agent worktrees and test
databases; `docs/build/README.md`, `docs/ARCHITECTURE.md`, `docs/deploy/REPORT.md`.
