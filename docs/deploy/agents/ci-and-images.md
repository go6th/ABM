# Agent 5 "ci-and-images": report

Branch `agent/ci-images` (from `feat/replica` b132645). Nothing was pushed, enabled or deployed. No
gcloud or terraform command was run; no paid or outside call was made (the hermetic stack runs on
the fake model and synthetic providers).

## 1. What I did

I built on the workflows the repository already had (GitHub Actions; same job names, same pinned
action SHAs, same Trivy 0.75.0 gate, same WIF variable names `GCP_PROJECT_ID`, `GCP_REGION`,
`WIF_PROVIDER`, `DEPLOYER_SERVICE_ACCOUNT`, `ARTIFACT_REGISTRY`, `CLOUD_RUN_*`).

### Design

1. **Build once, ship the bytes that were tested.** CI (`ci.yml`) builds api, worker and web, inspects
   them, scans them, runs them as a hermetic stack, and saves each image as an artifact
   (`image-<name>-<sha>`, 90 days). The deploy workflows download those artifacts, push them to
   Artifact Registry, read the digest, sign (keyless cosign) and deploy by digest. Nothing is
   rebuilt after the tests passed. (Before, CI pushed to GHCR and `scripts/deploy_gcp.sh`
   rebuilt different images for the deploy; the GHCR push is removed.)
2. **The hermetic research run happens in CI, before deploy** (`hermetic` job,
   `docker/ci/hermetic_stack.sh`), because staging refuses `LLM_FAKE` and `CONNECTORS=hermetic`. The
   job starts Postgres+pgvector, the migrate job, the fake IdP, worker, dev object store, api and
   web from the just-built images with plain `docker run`, seeds the synthetic demo workspace (two
   tenants; password generated per run, never printed), then: one research run (`hermetic_run.py`),
   an authenticated read-only cross-tenant probe (`probe_isolation.py`), and the replica Playwright
   spec with axe (1360 and 375).
3. **Against staging** (`smoke_deployed.sh`, after the rollout): `/healthz`, `/readyz` (the web asks
   the api), the sign-in page (and not "Sign in is unavailable"), an anonymous isolation probe (no
   credential and a forged token on a tenant route get 401/403; the Cloud Tasks route refuses a
   request without a Google OIDC token), the running service image equals the pushed digest, and
   the packaging import check run on the deployed api and worker digests. Staging has no test
   tenant credentials, so the authenticated cross-tenant probe is a CI-only step; the api is
   `INGRESS_TRAFFIC_INTERNAL_ONLY` and web is behind the load balancer, so everything is reached
   through `APP_URL` (the public web URL).
4. **Rollback** (`_deploy.yml`): before changing anything the workflow records the previous ready
   revision of api and web and the worker job image. If any step after the rollout fails (smoke
   test included) it runs `gcloud run services update-traffic ... --to-revisions=PREVIOUS=100` for
   both services and restores the worker job image. After a rollback traffic is pinned, so every
   deploy runs `update-traffic --to-latest` after the update. Migrations are forward-only, so the
   previous code must run on the new schema (expand, then contract).
5. **Production**: `deploy-prod.yml` is manual (`workflow_dispatch`, input `sha`). It requires the
   SHA to be on main, to have a green `ci` push run and a successful staging deploy, then calls the
   same reusable `_deploy.yml` with environment `production`, whose required reviewers are the
   owner's approval (the job cannot read the environment variables or authenticate until approved).
   It promotes the CI artifacts of that commit.

### Files

- `.github/workflows/ci.yml` (extended), `_deploy.yml` (new, reusable), `deploy-staging.yml`,
  `deploy-prod.yml` (rewritten to call it). `eval.yml` untouched.
- CI jobs: helm-web, lint-ci (actionlint, hadolint, shellcheck), quality (existing gates plus
  skill asset validation, migration lint, `alembic upgrade head` and `alembic check` on a scratch
  database, RLS catalog, `gen_isolation_matrix.py --check`, `gen_env_docs.py --check`,
  `check_routing_governance.py`), infra, web, e2e, dependency-audit (pip-audit via `uv export`;
  `npm audit --omit=dev --audit-level=high`), secret-scan (gitleaks v8.28.0 over full history),
  images (build, inspect, Trivy, SBOM, save), hermetic. Every job states `permissions:`; every
  third-party action is pinned to a full SHA with its version (I kept the existing SHAs, which I
  verified against the upstream tags, and added the missing version comments;
  `download-artifact` v4.3.0 d3f86a10...). Tool images are pinned by version tag, not digest.
- `docker/ci/`: `image_checks.sh`, `hermetic_stack.sh`, `hermetic_run.py`, `probe_isolation.py`,
  `smoke_deployed.sh`, `sign_images.sh` (the signing logic moved out of `scripts/deploy_gcp.sh`).
- Dockerfiles: `UV_HTTP_TIMEOUT=300 UV_CONCURRENT_DOWNLOADS=4` in the Python build stages (the
  first local build failed on a 30 s download timeout); `apt-get upgrade` in the Python runtime
  stages (a fixable HIGH, libpcre2 CVE-2026-103111, failed the Trivy gate on the base image);
  `web/Dockerfile` deletes `.next/**/*.map` after the build; `.dockerignore` also excludes
  `.github/ ops/ scripts/ evals/ docker/ compose*.yml *.map` and Terraform state.
- `.gitleaks.toml` (new, repository root; I judged it part of the secret scan): default rules plus
  a path allowlist of reviewed false positives (see section 3).
- `docker/cloudbuild.yaml` **deleted** (lead's item D11): CI is GitHub Actions, it pushed `:latest`
  tags to a hard-coded repo and region, scanned nothing and pinned no digest.

## 2. Checks run (all local, results exact)

| Check | Command | Result |
| --- | --- | --- |
| actionlint | `docker run --rm -v $PWD:/repo:ro -w /repo rhysd/actionlint:1.7.7` | clean (one SC2153 shellcheck note fixed on the way) |
| shellcheck | `docker run --rm -v $PWD:/mnt:ro -w /mnt koalaman/shellcheck:v0.10.0 docker/ci/*.sh` | clean after fixing SC2154/SC2015 |
| hadolint | `docker run --rm -i hadolint/hadolint:v2.12.0 hadolint --failure-threshold info - < <Dockerfile>` for api, worker, web | clean at info level |
| ruff | `uv run --frozen ruff format --check docker/ci && ruff check docker/ci` | clean |
| docker builds | `docker build -f docker/api.Dockerfile -t abm-agent-ci-api:check .` (worker the same; `docker build -f web/Dockerfile -t abm-agent-ci-web:check web`) | all three built (api 196 MB, worker 196 MB, web 105 MB compressed) |
| image inspection | `bash docker/ci/image_checks.sh {api,worker,web} abm-agent-ci-<n>:check` | all pass: User 10001 and `id -u` 10001; no `.env*`; no credential files or private key material in application files; no `.git`; no `*.map`; api/worker: no `/app/tests docs scripts ops evals`, no pytest/ruff/mypy/bandit/vulture/coverage/hypothesis/interrogate, no `uv`/`uvx`, no `/app/src`; web: no typescript/eslint/vitest/playwright/axe in node_modules, no web sources or tests, no npm/npx/corepack |
| packaging import check | `docker run --rm --entrypoint python <image> -I -c "import documents.sandbox_child, research_files.blobstore, devstore.server"` | succeeds in api and worker |
| Trivy gate | `docker run --rm -v /var/run/docker.sock:/var/run/docker.sock:ro aquasec/trivy:0.75.0 image --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 <image>` | api 0, worker 0, web 0 fixable HIGH/CRITICAL (final images) |
| Trivy totals (all, unfixed included) | `... image --scanners vuln --format json` | api and worker: HIGH 44, MEDIUM 63, LOW 62, UNKNOWN 1 (fixable: MEDIUM 5, LOW 1); web: CRITICAL 1, HIGH 47, MEDIUM 94, LOW 81 (none fixable). All the unfixed ones are Debian packages with no upstream fix. |
| web source maps today | `find /app -name '*.map'` in the first web build | **65 server-side maps in `.next/server/app`, no browser maps**; now deleted in the Dockerfile |
| hermetic stack | `bash docker/ci/hermetic_stack.sh up` then `check` (with `PACKAGE_PATH_SHIM=1`, see section 4) | seed OK; research run ended `PARTIAL` ("Complete with gaps"; accepted with `COMPLETED`); isolation probe: owner 200, other tenant 404, no credential 401 |
| anonymous probe | `python3 docker/ci/probe_isolation.py --api http://127.0.0.1:18090 --anonymous` | 401, 401, 403 |
| web in the built image | curl `/healthz` 200, `/readyz` 200, `/login` renders "Sign in" | pass |
| replica screens with axe | `npx playwright test -c playwright.replica.config.ts` against the built images | 6 passed (wide and phone) |
| pip-audit | `uv export --frozen --no-dev --no-emit-project ... ; uvx pip-audit@2.9.0 -r req.txt --no-deps --disable-pip --strict` | No known vulnerabilities found |
| npm audit | `npm audit --omit=dev --audit-level=high` in web/ | 0 vulnerabilities |
| gitleaks | `gitleaks git /repo --config .gitleaks.toml --redact` on a bare clone of the branch (410 commits) | 56 findings without the config (all vendor example requests in `docs/providers/sources/`, secret names in `scripts/gen_env_docs.py` and `infra/stack/main.tf`, synthetic test values); no leaks with the allowlist |
| guard tests | `PGPASSWORD=dev PGHOST=localhost PGPORT=5433 TEST_DB=ai_agent_ci_images_test uv run --frozen pytest -q -p no:cacheprovider tests/system/test_static_guards.py` | 5 passed |

## 3. Not done / limits

- The workflows were not run on GitHub (not enabled, not pushed); the staged parts that need GCP
  (WIF, push, cosign, gcloud, rollback, smoke on a real URL) are unrun by rule. actionlint is the
  only check on them.
- Local docker builds ran with the legacy builder (no buildx here); CI uses what ubuntu-latest has.
- `quality` additions (migration lint, alembic check, matrix, env docs, governance) were written
  from `scripts/check.sh` and the Makefile but not run end to end as a CI job here.
- The gitleaks allowlist is path based (the installed config version ignored `targetRules`); a real
  key in an allowlisted file would not be caught. Those files are listed in `.gitleaks.toml`.
- Base images are pinned by tag (`python:3.12-slim`, `node:22-slim`), not digest. Tool images are
  pinned by version tag. Digest pinning needs a bot (Dependabot/Renovate) to stay current.
- Trivy runs `--scanners vuln` only (as before); no misconfig/secret scanners.

## 4. Findings and handoffs

- **Real bug, found by running the built image (handoff to Agent 4, `src/`):** modules that find
  `config/` relative to their own file work from a source tree and break in the installed image.
  `POST /v1/runs` answered 500 (`FileNotFoundError: .../site-packages/../config/credits.yaml`)
  from `src/account_intelligence/replica/credits.py:40` (`parents[3] / "config"`). Same pattern:
  `src/account_intelligence/connectors/hermetic.py:32`, `replica/jobs.py:51-52` (also `skills/`,
  which does not exist at the repo root and is not in the image), `alerts/rules.py:20`, and the
  `ROOT = parents[2]` modules (`people/docs.py`, `exports/docs.py`, `privacy/retention.py`,
  `privacy/minimise.py`, `quality/regression.py`, `crosscut/conformance.py`,
  `account_intelligence/grading/docs.py`) if used at run time. Change: resolve through
  `CONFIG_DIR` (the images set `CONFIG_DIR=/app/config`). The compose stacks hide this because they
  mount `src` and `config` and set `PYTHONPATH`. CI's `hermetic` job runs without any mount, so it
  fails until this is fixed. `PACKAGE_PATH_SHIM=1 bash docker/ci/hermetic_stack.sh up` mounts
  `config/` at the wrong path only to let the rest be checked; do not set it in CI.
- **L10 (uvicorn access log):** not changed. The app has no request log of its own
  (`api/main.py` and `obs/logging.py` only log exceptions and sweeps), so turning
  `--no-access-log` on would leave no request log in the app. Handoff to Agent 4: add a
  request-completed log through the allow-list logger (method, route template, status, duration,
  request id, no query string); then add `"--no-access-log"` to the CMD in `docker/api.Dockerfile`
  and the uvicorn entrypoint of the worker. Cloud Run's own request log also records the URL; that
  is a platform setting for Agent 3.
- `docker/cloudbuild.yaml` removed. Handoffs: `DEPLOYMENT.md:84-85` (the `gcloud builds submit`
  lines) is the lead's doc fix; `scripts/deploy_gcp.sh` is now unused (the workflows do the work);
  delete it with `scripts/deploy_to_cloud_run.sh`, or keep it as a documented manual fallback.
  `make deploy-staging` and `docs/runbooks/deploy.md` should point at the workflows.
- `.github/workflows/ci.yml` expects the Makefile targets as they are today; `Makefile` `image`
  and `smoke` use `account-intelligence-*:local` tags (unchanged).
- `scripts/check_rls_catalog.sql` and `.claude/skills/account-intelligence-platform/scripts/
  check_rls_catalog.sql` differ. CI uses the skill copy, as `scripts/check.sh` does. Pick one
  (lead).
- `web/next.config.ts` (Agent 6): production source maps are off for the browser already; Next
  still emits 65 server maps. The Dockerfile deletes them; optionally set the matching Next option
  so the build does not write them.
- `.github/workflows/eval.yml` still uses the old tag-less pins; I did not touch it.

## 5. What the owner must create on GitHub

Environments: `staging` (no reviewers), `production` (required reviewers = the owner; restrict
deployment branches to main). Protect `main`: require the ci checks (lint-ci, quality, infra, web,
e2e, dependency-audit, secret-scan, images, hermetic) and the eval check.

Per-environment **variables** (names; values from Agent 3's Terraform outputs, `infra/modules/iam`):
`GCP_PROJECT_ID`, `GCP_REGION`, `WIF_PROVIDER` (full provider resource name
`projects/<number>/locations/global/workloadIdentityPools/<pool>/providers/<provider>`),
`DEPLOYER_SERVICE_ACCOUNT` (the deployer service account email), `ARTIFACT_REGISTRY`
(`<region>-docker.pkg.dev/<project>/<repository>`), `CLOUD_RUN_API_SERVICE`,
`CLOUD_RUN_WEB_SERVICE`, `CLOUD_RUN_RUN_JOB` (the worker job), `CLOUD_RUN_MIGRATE_JOB`, and new:
`APP_URL` (the public https URL of the environment, for the smoke tests).

**Secrets:** none. Workflows use only `github.token` (automatic). No key file; WIF only. The WIF
provider's attribute condition must allow this repository and, for production, the `production`
environment; the deployer needs Artifact Registry writer, `run.developer` on the services and jobs,
`iam.serviceAccountUser` on the runtime accounts, and permission to execute the migrate job.
The CSRF signing key secret Agent 3 adds is not referenced anywhere in the workflows.
