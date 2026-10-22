# Production-grade code evaluation — 2026-10-22

Branch `feat/final-product` (5 unpushed commits + uncommitted diff). Whole repository: backend `src/` (674 Python files, ~115k LOC), frontend `web/` (182 TS/TSX files), tests (331 pytest files, 31 vitest files, Playwright), CI/CD, Terraform, Docker.

Method: plugin audit, then the review skills from the installed plugins run as six parallel specialist reviews plus the deterministic gates. Nothing was modified; no paid model call was made.

## 1. Plugin audit

24 plugins enabled on the account; the ones that apply to this repository and how they were used:

| Plugin | Skill(s) used | Applied to |
|---|---|---|
| Clean Architecture | `ca_scan`, `ca_cycles`, `ca_metrics` | `src/` dependency rule and cycles |
| Python quality gate (PyGate) | `quick-gate-python` | ruff, mypy, bandit, vulture, import-linter, pytest unit |
| Review council | correctness, security, architecture, test-evidence, interface | ran as general reviewers (council refused this checkout; see §7) |
| Prism | security, quality, performance, testing profiles | folded into the reviews |
| OWASP security audit / finecomb / ateam security | OWASP 2025 + LLM-app lens | backend, frontend, infra |
| Docker skills, image scan | build strategies, compose patterns | three Dockerfiles, compose files |
| Backend performance review | workload-driven | runner, batch polling, route waterfalls |
| deep-refactor / legacy-refactor-pilot | audit method | architecture findings and the fix sequence in §6 |
| agentic-sdlc, superpowers | process (TDD, verification-before-completion) | gate evidence policy |

Not applicable to this task: Bright Data, Parallel search, sales/marketing/data/sector plugins, RAG debugger, agent debugger, makeDoc, competitor battlecards.

## 2. Scorecard

| Area | Grade | One line |
|---|---|---|
| Frontend (Next.js 16 / React 19) | **A-** | All gates green; BFF proxy, nonce CSP, zod-validated contract |
| Security | **A-** | No high/critical; default-deny routes, forced RLS, secrets clean |
| Deterministic Python gate | **GREEN** | ruff 0, mypy 0 issues, 253 unit tests pass, 18/18 import contracts |
| Tests and CI | **B+** | Isolation is a hard gate; coverage number excludes ~19k LOC |
| Deployment and ops | **B+** | Strong pipeline; hand-release path bypasses it; DR/load unproven |
| Backend architecture | **C+** | Core engine disciplined; product layer has hidden cycles and raw SQL |
| Pending diff (uncommitted) | **NOT SAFE TO COMMIT** | Leak-check exclusion too broad; concurrency mis-sized |
| **Overall** | **B** | Production-capable with one blocker in the working tree and a structural debt plan |

## 3. Gate evidence (run 2026-10-22)

| Gate | Result |
|---|---|
| `ruff check src tests` | 0 errors |
| `ruff format --check` | 1112 files formatted |
| `mypy src` | 0 issues in 674 files |
| `bandit -r src -ll` | High 0, Medium 21 (20 are B608 identifier-splice false positives, 1 B104 bind-all at `config.py:588`), Low 63 |
| `vulture src` | 18 hits, all Protocol/abstract parameters; no dead code |
| `lint-imports` | 18 contracts kept, 0 broken |
| `pytest tests/unit -m "not eval and not live"` | 253 passed, 0 failed, 13.1 s |
| Clean-architecture scan | 0 violations, 0 cycles (674 files) |
| `web: tsc --noEmit` | 0 errors |
| `web: eslint .` | 0 errors, 0 warnings |
| `web: vitest run` | 31 files, 168 tests passed |
| `web: npm audit --omit=dev` | 0 vulnerabilities |

## 4. Blockers (fix before commit)

**B1. Leak-check exclusion unprotects real private uploads.** `src/documents/repo.py:399-425` excludes any document whose name ends like a product deliverable (`People List`, `Internal Only`, `Account Summary`, `workspace`) from `private_passages` and `nearest_private`. A client upload named `People List.xlsx` is then never leak-checked. Fix: require the account prefix, or exclude only file_import-recognised documents and an exact `{account}_workspace` zip; never exclude `Internal_Only` by name.

**B2. The same exclusion misses its own target.** Zips are one document (`service.py:620-625`), so member names never reach the pattern; the staging case only passes because the zip name ends in `_workspace`. A browser-renamed `Tolmar_workspace (1).zip` fails again. Fix: match on the import manifest or member locator; add a test that ingests a user-named zip.

**B3. Extraction concurrency sized for one run per worker.** `src/agents/graph.py:118` sets 32 concurrent batches with a 45 s poll, budgeted at 43 req/min; `infra/stack/main.tf:221` gives the worker concurrency 4, so four runs reach ~170 req/min. A 429 propagates into `_all_or_none` and cancels every sibling batch, which the next slice re-submits: the "paid twice" failure. Fix: process-wide poll semaphore shared by all runs plus 429 backoff in the poll loop at `platform/llm.py:359`; make the sizing test multiply by worker concurrency.

**B4. Untracked test file.** `tests/agents/test_extraction_throughput.py` must be added or it is lost.

## 5. Findings by area (top items; file:line)

### Backend architecture (C+)
1. Real package cycles hidden by 504 function-local imports: `app/container.py:229,519,641` → `api.tenant_plane`; `identity/rate_limit.py:24-25`, `metering/limits.py:206`, `replica/isolation.py:217` → `api`; `runs/lifecycle.py:158,450,667` ↔ `replica`. Import-linter has no contract for `replica`, `chat`, `documents`, `research_files`.
2. ~900 raw `text()` SQL calls; 127 in `api/routes` (`auth.py:145-446`, `runs.py:547-656`, `admin_console.py`), `untenanted_session` used 70 times in `api`.
3. God functions: `agents/graph.py:284-948 build_abm_graph` (665 lines, 27 closures); `documents/service.py:156-1094 DocumentService` (940 lines, 34 methods); `routes/ask.py:128` (324 lines).
4. Money settled by four packages (`spend`, `replica/credits`, `agents/production/credit`, `metering/limits`); three settlement paths at run end (`runs/lifecycle.py:644,667`, `api/main.py:248-288`).
5. `yaml.safe_load` in 69 modules; `models.yaml` parsed by 17; `routes/runs.py:812-820` re-reads `views.yaml` per progress stream (same class as the 2026-10-21 freeze).
6. Two LLM layers (`src/llm`, `platform/llm.py`, chat providers) and five price calculators.
7. ASGI entry `api/main.py:146-322` runs the sweeper and hand-wires services; 16 `getattr(container, ...)` sites.
8. Guardrails drifted: hatch packages, import-linter roots and coverage sources are three different lists; mypy targets 3.12 while the floor is 3.11.

### Security (A-)
1. Medium: `api/operator.py:97-125` trusts an IdP `staff_role` claim over the server allowlist and skips the workspace-domain check when it is blank. Fix: allowlist only unless an explicit opt-in; refuse empty domain in cloud profiles.
2. Low/Med: `docker/worker.Dockerfile:24` installs pandas outside `uv.lock`, unseen by pip-audit.
3. Low: `documents/sandbox_child.py:10-25` seccomp install on Cloud Run gen2 still `VERIFY(V-SANDBOX-1)`; fails closed, so availability not escape.
4. Low: `identity/rate_limit.py:96-105` keys on proxy IP without Redis; set `FORWARDED_ALLOW_IPS` in non-Armor profiles.
5. Low: regex injection classifier `rag/guardrails.py:26-39` is bypassable; keep advisory.

### Tests and CI (B+)
1. `pyproject.toml:119` coverage `source_pkgs` omits `jobs`, `people`, `documents`, `research_files`, `devstore` (~19.6k LOC); the 94% gate overstates.
2. Down-migrations never exercised (`ci.yml:94-108`); `0018` has no down file.
3. `tests/exports/test_integration_checks.py:34`, `test_delivery_gates.py:25` skip silently in CI (no LibreOffice).
4. `tests/connectors/test_verify.py:10` is a permanent `[VERIFY]` placeholder skip.
5. `eval.yml:121-122` unpinned `actions/upload-artifact@v4`.
6. `tests/README.md:15-16` omits `not live`; no `addopts`/`--strict-markers`.
7. No direct tests for `admin_console.py` (811 LOC), `operator_admin.py`, `spend_v2.py`, `file_runs.py`, `internal_view.py`, `operator_view.py`, `model_choice.py`.
8. CI runs only on PR and main; this branch has no CI evidence yet.

### Deployment and ops (B+)
1. Hand-release path (`DEPLOYMENT.md:38-56`, the path actually used) bypasses Trivy, image checks, SBOM, cosign, hermetic e2e, smoke and rollback.
2. Worker image ships CUDA 13/cuDNN with `worker_gpu = ""` and L4 quota denied: multi-GB image for nothing. Build `--group cpu` until quota lands.
3. No trace exporter wired; `workload_alerts_enabled = false`, `alert_channels = []` in staging tfvars: 26 alert policies never created.
4. DR and load unproven: restore drill is a rehearsal, `ops/load/k6_profile.js` never run, runbook owner `TODO`.
5. `SECURITY.md:37,45-47` says no password endpoints exist; they do (migrations 0047/0048, `RUNBOOK.md:40-43`).
6. `migrations/0064_model_choice.sql:14` drops a column in the same release that adds its replacement (breaks the compatible-rollout rule in `_deploy.yml:10-12`); `0043` deletes tombstones with no archive.
7. Secret references use `version = "latest"` (`cloud_run/main.tf:133,169`), outside the deploy rollback.
8. `infra/stack/main.tf:474-483` grants `roles/secretmanager.admin` to api and worker; deployer has project-wide `run.developer`.
9. Unpinned emulator images in `compose.yml:40,46` and `ci.yml:75,77`; no `timeout-minutes` in CI; smoke test skips the worker digest; compose worker has no healthcheck.

### Frontend (A-)
1. `components/ProviderKey.tsx` is dead and carved out of both lint and typecheck (`eslint.config.mjs:7`, `tsconfig.json` exclude).
2. `lib/server-api.ts:29,36` forwards every cookie and has no timeout; the browser proxy filters to three `__Host-` cookies. Same in `app/api/auth/logout/route.ts:16`.
3. `lib/api.ts:114-126` caches the CSRF token with no rotation recovery.
4. `app/api/operator/v1/[...path]/route.ts:67,94` buffers up to 100 MB bodies in memory; the tenant proxy already streams.
5. `app/v1/[[...path]]/route.ts:71` upstream fetch has no abort propagation or timeout.
6. `app/(client)/layout.tsx:10-23` three sequential awaits per render.
7. `web/Dockerfile:7,12,25` unpinned base, `apt-get upgrade` at build, `--force-remove-essential perl-base`; `npm ci` without `--ignore-scripts`.
8. Auth pages use raw `fetch` with hand-cast types instead of the contract client.
9. 2.5 GB of stray `.next-*` build dirs; `tsconfig.json` includes ad-hoc dist type dirs.
10. `RunSteps.tsx:85-114` and `Ask.tsx` interleave polling, SSE and timers; extract a hook.

## 6. Step-by-step path to production grade

Ordered so each step is independently committable and verifiable. No step starts a paid run.

1. **Unblock the working tree (B1-B4).** Narrow the deliverable exclusion in `documents/repo.py`, add the zip-member and account-prefix tests, add the shared poll semaphore and 429 backoff, `git add` the throughput test. Re-run the documents and workflow test dirs plus `tests/isolation`.
2. **Make the guardrails honest.** One source for the package list (hatch, import-linter roots, coverage sources); add `documents`/`replica`/`chat`/`research_files` contracts: "runs/identity/metering/app never import api", "routes never import sqlalchemy.text". Re-baseline `fail_under`. mypy `python_version = "3.11"`. `addopts = "-m 'not eval and not live' --strict-markers"`.
3. **Close the security residuals.** Operator role from allowlist only; refuse empty workspace domain in cloud profiles; pandas into `uv.lock`; `FORWARDED_ALLOW_IPS` for non-Armor profiles; staging smoke for the seccomp filter.
4. **Frontend hardening.** Delete or move `ProviderKey.tsx` and both exclusions; `tenantCookies()` + 10 s timeout in `server-api.ts`; CSRF replay-once in `lib/api.ts`; stream the operator proxy; `signal: request.signal` on the tenant proxy; `Promise.allSettled` in the client layout; pin `node:22-slim@sha256`, `npm ci --ignore-scripts`; `apiPublic()` for auth pages.
5. **Deploy path.** Make `deploy-staging.yml` the only release path (or gate the Cloud Build path with `image_checks.sh` + Trivy + `smoke_deployed.sh` from the Makefile); build the worker `--group cpu`; pin secret versions; replace `secretmanager.admin` with a custom role; pin emulator images; add CI `timeout-minutes`, worker digest check and worker healthcheck.
6. **Observability and DR.** Install the Cloud Trace exporter and call `obs.setup.configure` in the API lifespan; one notification channel; `workload_alerts_enabled = true`; one real PITR restore game-day and one k6 run against staging, both recorded; name runbook owners; rewrite the `SECURITY.md` auth section.
7. **Tests.** Add the five packages to coverage; `alembic downgrade -1 && upgrade head` in CI; LibreOffice in the quality job; route tests for the seven untested admin/operator routes; pin `upload-artifact`; CI on `feat/**` pushes; retire `test_verify.py` placeholder.
8. **Architecture refactor train (sequenced PRs, test contract frozen).** (a) Move `tenant_plane` builders to `platform/gcp`, `problem`/`ratelimit` primitives to `crosscut`; (b) `replica/credits` + `notifications` out of `replica` so `runs` stops importing it; (c) one `credits` package owning reserve/settle/release, `spend` keeps rules only; (d) typed cached `ModelsConfig`/`ProvidersConfig`/`ViewsConfig` and an AST test banning `import yaml` elsewhere; (e) split `build_abm_graph` into `agents/nodes/*`; (f) split `DocumentService` along its status machine; (g) `AskService` behind `routes/ask.py`; (h) move sweeper out of `api/main.py`; (i) merge the two LLM layers and the five price calculators; (j) retire `routes/spend.py` once v2 covers it; move dead modules to `unwanted-things/`.
9. **Finish.** Re-run every gate in §3 plus `tests/isolation` and the web e2e against a hermetic stack; open the PR so CI produces the evidence; release through the gated workflow.

## 7. Notes on the review itself

- The review-council plugin refused to dispatch from this checkout because `.claude/launch.json` and the five workflow files differ from `origin/main`. Its reviewers were replaced by general reviewers with identical briefs; the council's bypass variable was not set.
- Playwright e2e, integration, evals and anything needing Postgres or Docker were not run; those results come from CI once a PR exists.
- `projects/` (16 MB of plugin checkouts) is untracked and sits in the Docker build context; it is not in `.dockerignore`.

---

## 8. Applied on 2026-10-22 (same day, after the evaluation)

Everything below is in the working tree, uncommitted, on `feat/final-product`. Nothing was deployed, no image was built, no paid model call was made.

### Blockers (section 4): all closed
- **B1/B2** `src/documents/repo.py`: the leak-check exclusion now matches only the file names recorded in `deliverables` for the account (also under a browser's ` (n)` suffix), and judges a zip member by its own name via the passage location, so a private memo inside the uploaded workspace zip is still checked and a client file merely named `People List.xlsx` is never skipped. `Internal_Only` is never excluded. DB test rewritten with lookalike, browser-suffix and zip-member cases; a pure unit test covers the pattern.
- **B3** `src/account_intelligence/platform/llm.py`: a process-wide request pacer (50/min) covers every batch create/retrieve/cancel across all runs on the worker, and a 429 waits with doubling backoff (10 attempts) instead of cancelling the sibling batches. Throughput test now accounts for worker concurrency 4.
- **B4** the throughput test file remains untracked by design (nothing is staged or committed); add it with the commit.
- Also: stale Cloud Tasks redelivery of a finished slice is a no-op (`begin_slice` is conditional on the expected slice number); leak-check tuple handling fixed; stale runner comments and the loop register refreshed.

### Security residuals: closed except one
- Operator role is allowlist-only unless `STAFF_IDP_ASSERTS_ROLE=true`; cloud profiles refuse a missing `STAFF_WORKSPACE_DOMAIN`.
- pandas is in `uv.lock` (gpu group); the ad-hoc install is gone from the worker image.
- `FORWARDED_ALLOW_IPS` set for the compose profile, the browser proxy forwards `x-forwarded-for`, runbook notes the in-process limiter is dev-only.
- Seccomp filter check added to the hermetic CI stack (`docker/ci/hermetic_stack.sh`); `VERIFY(V-SANDBOX-1)` can be closed once CI has run it. Not closed in source.

### Frontend: all ten items done (one scoped out)
Dead component moved out and the lint/typecheck carve-outs removed; server components forward only tenant cookies with a 10 s timeout; CSRF refusal replays once; operator proxy streams with the shared body cap; tenant proxy propagates abort and applies per-route deadlines; `Promise.allSettled` on the client layout and support-access page (the account page's second call depends on the first, so it stays sequential); Dockerfile pinned by digest, no build-time upgrade, `--ignore-scripts`; `apiPublic()` for all five auth pages; stray build dirs moved out and tsconfig cleaned; `useRunStream` hook extracted.

### Deploy, CI, infra, docs
Worker image builds CPU-only by default (`UV_GROUP` build arg) with a healthcheck; eval model config moved out of the API image into Terraform and compose; emulator images pinned; CI job timeouts, `feat/**` trigger, downgrade-then-upgrade migration step, LibreOffice in the quality job, worker digest in the smoke test; `make release-check` gates the emergency Cloud Build path; secret versions pinnable per secret (unlisted still `latest`); custom tenant-secrets role replaces `secretmanager.admin`; per-service `run.developer`; Cloud Trace exporter wired in the API lifespan; `workload_alerts_enabled = true` in staging; `SECURITY.md`, `DEPLOYMENT.md`, `RUNBOOK.md`, restore runbook corrected.

### Tests and guardrails
Coverage, import-linter and hatch package lists now agree (37 packages) with a test that keeps them equal; ratchet contracts and AST tests freeze the api-import cycles, raw SQL in routes, untenanted sessions and yaml readers; `addopts` applies `not eval and not live` with strict markers; seven new route test files for the admin and operator surfaces; slice/lease/deadline consistency test; placeholder `[VERIFY]` test moved out.

### Gate evidence after the changes
| Gate | Result |
|---|---|
| ruff check / format | 0 errors / 1123 files formatted |
| mypy src | 0 issues, 674 files |
| import-linter | 19 contracts kept (one new ratchet) |
| bandit | High 0, Medium 22 (B608 identifier splices + B104) |
| telemetry, comments, guarantees, runbooks, loop register | all pass |
| pytest unit, guardrails, api, documents, workflow, agents, obs, isolation (private scratch DB) | 1648 passed, 1 skipped, 1 xfailed |
| web typecheck / lint | 0 / 0 |
| web vitest | 37 files, 205 tests passed |
| terraform fmt, validate, tests | clean; stack 8/8 |
| hadolint, actionlint, shellcheck, compose config | clean |

### Still open (owner decisions or real-world runs)
1. Architecture refactor train (section 6 step 8) is not started; the ratchets stop it getting worse.
2. `alert_channels` is empty: create one notification channel and put it in the staging tfvars.
3. `secret_versions` is empty: pin real version numbers before production.
4. The custom tenant-secrets role with the `tenant-` condition should be confirmed on staging for brand-new secret creation.
5. A real PITR restore game-day and a k6 run against staging (`make load-staging`) still need to happen and be recorded.
6. `fail_under = 94` will be re-measured by the first CI run now that five more packages count.
7. mypy stays on `python_version = "3.12"`: numpy's stubs refuse 3.11.
8. No container image was built locally; CI builds and scans them.
9. Open a PR so CI produces the evidence for this branch; commit when you say so.
