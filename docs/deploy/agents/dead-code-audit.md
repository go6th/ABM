# Agent 2 "dead-code-audit": log

Read only audit of `/home/sakib/abm-replica` (branch `feat/replica`), 2026-10-14. Wrote
`docs/deploy/DEAD_CODE.md` and this file; nothing else. No git command that changes state, no
`npm install`, no Docker, no gcloud, no terraform, no database, no paid call, no `.env*` file opened.
`git status --short` was empty before and after.

## Commands and results

| Step | Command (from `/home/sakib/abm-replica` unless stated) | Result |
| --- | --- | --- |
| Plan and prompt | read `docs/deploy/PLAN.md`; read FIX_PROMPT (scratchpad copy) lines 30-113 | Phases 5 to 7 list captured (PLAN section 2) |
| Python dead code | `uvx vulture src scripts --min-confidence 80` | 0 findings (matches the CI gate, `Makefile:104`) |
| Python dead code | `uvx vulture src --min-confidence 60 > /tmp/da/vulture60.txt` | 1,025 lines; 205 unused functions/classes outside `api/routes/` and `ports.py`; noisy for route handlers and pydantic fields |
| Web dead code | `npx --yes knip --no-config-hints` in `web/` (output `/tmp/da/knip.txt`) | 51 unused files, 6 unused dependencies, 2 unused devDependencies, 1 unresolved import (`@/components/ws/FileUploader` in `tests/unit/research-files.test.tsx`), 325 unused exports (305 in generated `lib/gen/api.gen.ts`), 7 unused exported types. No cache or config file was written (git status clean) |
| Web lint | `npx eslint .` in `web/` | 0 errors, 1 warning (`web/.probe.mjs`, unused `tall`) |
| Route usage | `/tmp/da/routes.py`: for each of the 182 OpenAPI paths, prefix match against live web files (not knip-unused, not in `LEGACY_TO_DELETE.txt`, not `e2e/parity` or `e2e/review`), dead web files, Python tests and the isolation matrix (`/tmp/da/routes.txt`) | 77 paths with `live=0` (includes `/healthz`, called by infra); 11 `/v1/files*` paths have only dead web or test callers |
| Route usage (precise) | per-suffix greps for runs and accounts sub-routes (`/approve`, `/cap`, `/collected`, `pull-option`, `/tables/`, `/history` ...) | `pull-option`, `/history` and `/tables/` have no live caller; `cap` and `approve` only in `RunSteps.tsx` |
| Python module graph | AST/regex pass over `src/**/*.py` for modules no other `src/` module imports; second pass listing who else uses them | about 60 modules, nearly all used only by tests, `scripts/check_conformance.py` or `section11_spec.yaml` |
| research_files reachability | pass over `src/research_files/` with roots outside the package and outside `routes/research_files.py` | reachable from `documents`: `blobstore`, `checks`, `config`, `detect`, `parsers/*`, `repo.log_call`; unreachable: `analytics`, `modelsvc`, `pipeline`, `profile`, `qa`, `service`, `upload` |
| Config loaders | for each of 150 files under `config/`: grep for the file name in `src scripts Makefile infra docker .github compose*.yml pyproject.toml web` and tests | 29 with no direct hit; after checking loaders by directory and `section11_spec.yaml`, 4 truly unloaded (D12) |
| Scripts | for each file in `scripts/`: grep for the file name in Makefile, workflows, docker, compose, infra, tests, src, docs runbooks, `package.json` | 8 with 0 references; docs-only references checked for each (D10, D25 are the removals) |
| Env registry | compared `docs/ENVIRONMENT.md` names with setters in compose, infra, `.env.example`, workflows | only the documented "Retired" names are unread; `MODEL_ROUTER` is set nowhere |
| Duplicates | AST hash of function bodies across `src/` (exact duplicates: 1, `sleep` in `container.py` and `parallel_phase.py`); same-name private helpers; domain normaliser grep | S10 |
| Hard-coded values | greps for model ids, URLs, regions, project names, UUIDs, `localhost` in `src/` and `web/` | S4, S7; no model id in code |
| Exceptions | script over `src/` for `except Exception/BaseException/:` with no comment within two lines | 28 without a reason comment, 5 that swallow without logging; 0 bare `except:` (S9) |
| Route handlers | counted `text(` per route file | S8 |
| Conformance | `uv run --frozen python scripts/check_conformance.py` | not runnable: `KeyError: 'ADMIN_DSN'` (needs a database). The spec was read instead |

## What I could not do, and why

- No test, catalog or isolation run (read only, no test database), so the claim that D1 leaves the checks green is
  by reading, not by running. Agent 4 must run `scripts/check_route_catalog.py`, the isolation matrix tests, `check_guarantees.py` and
  `tests/system/test_single_write_paths.py` after removing D1.
- `ts-prune` was not needed (knip covered it). knip ran without a project config.
- vulture 60% output is heuristic. I used it to find candidates, never as the proof.
- I did not read user-facing docs for prose mentions of removed features, only grepped by path and name.
- Not examined in depth: `infra/**` (Agent 3), `.github/workflows/**` (Agent 5), migrations. Only the helm chart and cloudbuild file were
  considered because of their use in CI and docs.

## Hand-offs (not my files)

- `DEPLOYMENT.md:67,85`: references `deploy_to_cloud_run.sh` and `docker/cloudbuild.yaml` (D10, D11). Lead.
- `docs/infrastructure-plan.md:3`: mentions `config/{dev,staging,production}.yaml` (D12). Lead.
- `docs/UI_REMOVED.md`, `docs/UI_BACKEND_AUDIT.md`, `docs/frontend/operator-route-map.md`: say the four operator routes are "hidden in production" (D2). Lead.
- `docs/SCREEN_MATRIX.md`, `docs/build/VISUAL_NOTES.md`, `docs/WIRING.md` (regenerated): mention parity and review specs, `visual-shots.mjs`, dead call sites (D4, D7). Lead.
- `config/section11_spec.yaml:35`: stale pointer to `src/gate/pipeline.py` (S12). Agent 4.
