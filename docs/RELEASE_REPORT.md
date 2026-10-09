# Release audit report

Audit date: 2026-10-09. Branch: `chore/finalize-clean-deploy`. No push, cloud action, real provider
call, or real credential check was performed. Results below distinguish recorded runs from gates
that were not run or did not pass.

## Gate results

| Gate | Result | Evidence |
|---|---|---|
| Backup | PASS | `../backup-before-finalize-20261008-164539.zip`; non-empty and ZIP integrity check passed; tag `backup/pre-finalize-20261008` points at the pre-audit commit. |
| Specification matrix | PARTIAL | `docs/TRACEABILITY.md` contains source-linked requirements and conservative statuses; original source files are local and gitignored. Matrix completeness has not been independently checked requirement-by-requirement against every source page. |
| Deletions | PARTIAL | Removed archived prompt docs, an unreferenced review-store stub, an obsolete deploy stub, and obsolete login screenshots in reviewed commits. Per-category fast checks ran; complete duplicate/dead dependency scans and full final cleanup are not done. |
| Structure | PARTIAL | Existing `src/`, `web/`, `migrations/`, `infra/`, `config/`, `scripts/`, and `docs/` retained. Compose declares an API container, but `make up` starts only dependencies and API startup from a clean checkout with bootstrap/migrations was not proven. Worker, web, scheduler, fake IdP, provider/model stubs, and notification sinks are absent. |
| Wiring | PARTIAL | `scripts/wiring_audit.py --check`: passed static cross-check of 69 frontend call sites; zero static breaks. It does not run the stack, enumerate every running OpenAPI policy/schema detail, or prove browser flows. |
| Environment | FAIL | No single typed registry for all environment readers; `.env.example` is not generated from one registry; the full synthetic stack is absent. The `doctor` target exists but reports unavailable API/web/fake IdP and missing host `pg_isready`. `scripts/preflight_deploy.py` checks only reviewed Terraform inputs and secret references. |
| Tests | FAIL | Three captured post-fix `TMPDIR=/dev/shm make test` runs passed. The latest: isolation 378/378 in 53.08s; backend 3,343 passed, 12 skipped, 2 deselected, 4 warnings, 94.17% coverage in 1,454.45s; exit 0. The configured backend suite is green, but browser E2E, API fuzz, full accessibility, production-load, and backup/restore are not proven. |
| Deployability | FAIL | The existing API image built with a 7.391 MB context and passed a non-root/readiness/graceful-shutdown smoke. Terraform format/validation passed with `-backend=false`. Separate API/worker/migrator/web images, vulnerability scan, SBOM/signing, full Compose run, CI configuration lint, and cloud deployment were not proven. |
| Cleanup | PARTIAL | Reviewed prompt/docs/screenshots removed and references updated. Generated caches, coverage data, mutmut output, Terraform cache, Playwright results, and the Next.js build output were removed after the final run. User-owned documents, local environment/tool directories, and `.env` were preserved for review. |

## Recorded checks

| Check | Result |
|---|---|
| Skill self-test | `TMPDIR=/dev/shm .claude/skills/account-intelligence-platform/scripts/selftest.sh` passed. |
| Backup integrity | ZIP archive listing and integrity check passed; archive size 88.5 MB. |
| Web unit tests | `npm run test`: 20 passed. |
| Web typecheck and lint | `npm run typecheck`; `npm run lint`: passed. |
| Web production build | `TMPDIR=/dev/shm npm run build`: passed; standalone production browser smoke on login pages, favicon, and browser console/network errors passed. |
| Backend unit tests | `TMPDIR=/dev/shm uv run --frozen pytest tests/unit -x -q`: 89 passed. |
| Full test history | Earlier full runs had 30 failures, then 28 failures while fixes were being made. Three captured post-fix runs then passed. A further post-fix invocation lost its terminal session before a final summary was captured and is not counted. Failures were corrected in code/fixtures, with no xfail or assertion weakening. |
| Full configured backend suite, pass 1 | `TMPDIR=/dev/shm make test`: isolation 378 passed in 102.15s; backend 3,340 passed, 15 skipped, 2 eval tests deselected in 2,932.58s; coverage 94.06%; exit 0. Four pytest warnings remain for synchronous tests carrying an asyncio mark. |
| Full configured backend suite, pass 2 | `TMPDIR=/dev/shm make test`: isolation 378 passed in 263.21s; backend 3,343 passed, 12 skipped, 2 eval tests deselected in 3,118.23s; coverage 94.17%; exit 0. |
| Full configured backend suite, pass 3 | `TMPDIR=/dev/shm make test`: isolation 378 passed in 53.08s; backend 3,343 passed, 12 skipped, 2 eval tests deselected in 1,454.45s; coverage 94.17%; four warnings; exit 0. Output and exit code 0 saved at `/tmp/abm-final-full-test-20261009.log` and `/tmp/abm-final-full-test-20261009.exit`. |
| Full-run skip reasons | The latest two full runs skipped 12 provider verification cases because source claims remain unresolved. The first post-fix run also skipped 1 Redis case and 2 emulator contract cases because those local services were offline; after `make up`, focused Redis/GCS/Cloud Tasks tests passed 9/9. |
| Focused post-fix regressions | 191 passed across RAG, export integration, regression policy, and table-writer ownership. PostgreSQL-backed phase 4 simulation/alerting and parallel cap/pause: 16 passed. |
| Focused DSAR workflow | `pytest tests/crosscut/test_x4_privacy.py::test_a_dsar_runs_end_to_end -x -q --tb=short`: 1 passed on a fresh migrated database. |
| `make lint` | Passed: Ruff format/check and mypy strict across 559 source files. |
| `make types` | Passed: mypy strict across 559 source files and web TypeScript. |
| `make static` | Passed: 18 import contracts; 100% interrogate coverage; comments/guarantees/loops; Bandit; vulture; compileall. Bandit emits existing `nosec` and parser warnings for reviewed false positives. |
| `make doctor` | The target exists, but the local doctor run is not green: API, web, and fake IdP services are unavailable; the host lacks `pg_isready`. It reports gaps without printing environment values. |
| `make contract` | Passed: OpenAPI snapshot, generated web client, route catalog, and wiring report checks; 69 frontend call sites, zero static breaks. |
| RAG metric tests | 21 passed after fixture/expectation corrections. |
| Dead code | `uv run --frozen vulture src scripts/vulture_whitelist.py --min-confidence 80`: passed. |
| Migration lint | `uv run --frozen python .claude/skills/account-intelligence-platform/scripts/lint_migrations.py migrations`: 84 files passed after migration 0043. |
| Empty-database migration/RLS | `alembic upgrade head` twice, zero-row `check_rls_catalog.sql`, `alembic downgrade -1`, and upgrade twice again: passed on a disposable scratch database. |
| Terraform | `make infra-fmt infra-validate`: passed for dev/staging/production with Terraform 1.9, `-backend=false`; no cloud credentials or apply. |
| Runbook consistency | `uv run --frozen python scripts/check_runbooks.py`: passed. |
| Deployment preflight | Passed with synthetic valid Terraform inputs and rejected placeholder inputs; it did not read or print secret values. |
| Frontend/backend static wiring | `uv run --frozen python scripts/wiring_audit.py --check`: passed static audit; 69 frontend call sites, zero static breaks. |
| Docker build and smoke | `make image` passed with a 7.391 MB context after adding `.dockerignore`; migrated scratch DB then `scripts/smoke_image.sh` passed as UID 10001, `/readyz` 200, graceful SIGTERM. Scratch database dropped afterward. |
| Production browser requests | `/login`, `/operator/login`, and `/icon.svg` returned 200; no console errors, failed requests, or error responses observed. This is not an authenticated user-flow test. |
| Browser visual review | Earlier production Playwright review captured and inspected customer/operator desktop light screens and a customer phone-width dark screen; a later review inspected both dashboard empty states. Axe reported zero WCAG 2.1 A/AA violations on those four screens. Images were kept in temporary storage, not the repository. |

The intermediate failed run exposed export conversion under a constrained temporary directory,
fixture rows that did not satisfy the existing Hypothesis question constraint, incomplete RAG
refusal/confidence handling, tracker status-only updates that dropped prior proof references, and
stale table-writer ownership metadata. These were corrected and the named failing groups passed in
the final run. The property backoff test's default 200 ms Hypothesis deadline was removed because
the test asserts a formula invariant, not execution speed; its assertion and generated cases remain
active. No failures were suppressed or skipped to obtain the green final backend run.

## Traceability summary

Statuses are counted from `docs/TRACEABILITY.md`; they remain conservative after the test fixes
because the full stack and authenticated user journeys are not proven. The matrix has 181 rows: 1
Done, 161 Partial, 13 Missing, and 6 Blocked-by-external. `Done` means the mapped check passed, not
that production operation has been proven. Configured backend coverage ranged from 94.06% to 94.17%
across the three captured post-fix runs (project threshold 80%).

## Cleanup counts

| Category | Deleted |
|---|---:|
| Archived prompt documents | 14 |
| Unreferenced implementation stub | 1 |
| Obsolete deployment stub | 1 |
| Obsolete screenshots | 2 |
| Dead configuration/API option | 1 |
| Total deleted files | 18 |

One additional dead API option was removed without deleting its containing file. These counts refer
to committed repository deletions so far, not ignored cache/build output. The backup ZIP is outside
the repository at `../backup-before-finalize-20261008-164539.zip`.

## Final tree and inventory

The product tree (excluding `.git`, `node_modules`, and `.venv`) is organized as follows:

```text
.
├── .claude/skills/account-intelligence-platform/
├── .github/workflows/
├── config/
├── docs/ (API, architecture, decisions, runbooks, sources, status, traceability)
├── evals/
├── infra/ (Terraform environments and modules)
├── migrations/ (SQL and Alembic versions)
├── ops/
├── scripts/
├── src/
├── tests/ (unit, integration, isolation, system, e2e, fixtures)
├── web/ (Next.js app, components, library, tests)
└── root files: Dockerfile, Makefile, compose.yml, README.md, BUILD_LOG.md,
    pyproject.toml, uv.lock, .env.example, and project policy/config files
```

| Disposition | Top-level paths and file groups | Reason |
|---|---|---|
| KEEP | `.git`, `.claude`, `.github`, `config`, `docs`, `evals`, `infra`, `migrations`, `ops`, `scripts`, `src`, `tests`, `web`; root files `.dockerignore`, `.editorconfig`, `.env.example`, `.gitattributes`, `.gitignore`, `.pre-commit-config.yaml`, `BUILD_LOG.md`, `CONTRIBUTING.md`, `Dockerfile`, `Makefile`, `NOTICE`, `README.md`, `SECURITY.md`, `alembic.ini`, `compose.yml`, `pyproject.toml`, `run_sql.py`, and `uv.lock` | Product source, tests, deployment materials, policy, and required development guidance. `docs/sources/` holds the three local, gitignored source specifications. |
| REVIEW / LOCAL ONLY | `.env`, `.freebuff`, `.kilo`, `.venv`, `venv`, `venv2`, `ABM_Skill_Readers_Guide_v2.docx`, its LibreOffice lock file, and installed `node_modules` | Preserved because they contain local credentials, tool state, environments, dependencies, or a user-owned guide; excluded from commits and the printed product tree. `.env.example` remains a tracked template and contains no real values. |
| DELETE | The 18 committed files in the cleanup table above; generated cache/build/test outputs listed in the cleanup gate | Removed after backup and review. No `docs/prompts/`, repository ZIPs, screenshots, coverage files, or mutmut output remain in the product tree. |

After the audit-specific commits, `git status --short` still reports 115 modified or untracked
worktree entries. They were left unstaged because the shared workspace contained substantial
project work whose ownership was not clear enough to include in these commits. Review those entries
before merging or publishing; no push was made.

## Remaining release blockers and owner inputs

- Make the declared API container start reliably from a clean checkout with DB bootstrap and
  migrations; add worker, scheduler, web, seeded fake OIDC users, scripted model/provider stubs,
  and email/Slack sinks; run browser E2E across roles and tenants.
- Add a single typed environment registry and generated `.env.example`; finish settings/secrets
  scanning and a useful `make doctor`.
- Split and validate API, worker, migrator, and web images; add image vulnerability scan, SBOM,
  signing, and CI configuration validation.
- Run an owner-specific no-backend plan review, CI configuration lint, and image scan/SBOM/signing
  before any staging change.
- Replace or explicitly scope the no-op audit/metrics modules in `src/metrics/`, `src/registers/`,
  and `src/unitcost/`; their current functions contain `pass` and are not production behavior.
- Complete requirement-by-requirement source review; resolve missing/partial product requirements
  and legal/consent decisions listed in `docs/open-items.md` and `docs/decisions/conflicts.md`.
- Supply GCP project IDs, regions, billing and data residency, WIF provider/service account,
  protected-environment approvers, OIDC/Workspace details and domains, Secret Manager references,
  provider registrations/keys, model choice, and service on-call owners.
- Real provider calls, real IdP sign-in, cloud plan/apply/deploy, external penetration testing,
  production load, regional failover, and real pilot tenant testing remain unperformed.

## Next safe deployment step

Complete the local release blockers above, then run `make infra-fmt infra-validate` and
`scripts/preflight_deploy.py --env staging` with the owner's non-secret staging Terraform inputs.
Review the Terraform plan in a human change window. This report does not authorize or run a cloud
change.
