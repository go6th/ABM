# Dead code audit (Agent 2, read only)

Repository `/home/sakib/abm-replica`, branch `feat/replica`, audited 2026-10-14. Nothing was edited
except this file and `docs/deploy/agents/dead-code-audit.md`. Commands and result counts are in the
agent log; the proof for each candidate is below. Scratch output was kept under `/tmp/da/`.

How "dead" was decided, for every candidate:

1. no import (grep over `src/`, `scripts/`, `tests/`, `web/`; `vulture` for Python, `knip` for web);
2. no catalogued route that the web app calls (live web code only, see "Live web" below) and no
   route that a non-test caller needs;
3. no config that loads it;
4. no task, script, Makefile target, workflow or compose service that runs it;
5. FIX_PROMPT Phases 5 to 7 and `docs/deploy/PLAN.md` section 2 do not need it.

"Live web" means a web file that `knip` reaches from the Next.js entries (pages, route handlers,
`proxy.ts`) and that is not on the tsconfig, eslint and vitest exclude lists. `wiring_audit.py` counts
call sites in files that are excluded from the build, so its "frontend call site" numbers overstate
what the browser can reach; `docs/WIRING.md` itself says its 138 uncalled operations are "not an orphan
deletion list", and this report follows that: a route with no web caller is not removed on that fact
alone.

Verdict words: **remove** (dead, removal is self-contained, a wave 2 agent acts now),
**keep (why)**, **remove after Phases 5 to 7** (dead or contradicting the replica, but FIX_PROMPT
Phase 6 or a later phase owns it, or removal needs an owner decision).

## 1. Summary table

| # | Candidate | Verdict | Owner | Files (detail in section 2) |
| --- | --- | --- | --- | --- |
| D1 | Older research-files upload path (`/v1/files/*`, `/tasks/file-process`, file sweep, 7 modules, worker wiring) | remove | Agent 4 (src, tests, config); Agent 6 (web part, D1w) | `src/account_intelligence/api/routes/research_files.py`; `src/research_files/{analytics,modelsvc,pipeline,profile,qa,service,upload}.py`; most of `src/research_files/repo.py`; wiring in `container.py`, `main.py`, `tasks.py`, `hermetic.py`, `gcp/adapters.py` |
| D1w | Web side of D1 (stub pages, `lib/research-files.ts`, `lib/file-generator.ts`, its excluded test) | remove | Agent 6 | `web/app/(client)/research-files/**`, `web/lib/research-files.ts`, `web/lib/file-generator.ts`, `web/tests/unit/research-files.test.tsx` |
| D2 | "Unavailable operator routes" block in `web/proxy.ts` | remove | Agent 6 | `web/proxy.ts:34-44` |
| D3 | Tracked probe scripts `web/.probe.mjs`, `.csp.mjs`, `.gal.mjs`, `.shots.mjs` | remove | Agent 6 | those four files |
| D4 | Dead web components, libs and operator-console files (knip plus build excludes) | remove | Agent 6 | list in section 2 (D4) |
| D4b | Dead operator provider-labels route and lib | remove | Agent 6 | `web/app/api/operator/providers/route.ts`, `web/lib/provider-labels.ts` |
| D5 | Redirect-stub pages not named in FIX_PROMPT 6.1 | remove | Agent 6 | `accounts/[domain]/history`, `accounts/[domain]/tables/[table]`, `audit` (+layout), `feedback` (+layout), `operator/(console)/configuration`, `operator/(console)/failures` |
| D6 | Redirect-stub pages named in FIX_PROMPT 6.1 | remove after Phases 5 to 7 | Agent 6 (Phase 6) | `alerts`, `chat`, `downloads`, `runs`, `usage` (+layout), `settings/*`, operator `spend`, `cost`, `tenants`, `support` |
| D7 | Excluded Playwright suites, configs and probe scripts (`e2e/parity`, `e2e/review`, parity and review configs, `screenshot_test.spec.ts`, 3 shot scripts) | remove | Agent 6 | section 2 (D7) |
| D8 | Vitest files that test only dead code | remove (2 files) | Agent 6 | `web/tests/unit/research-files.test.tsx`, `web/tests/unit/run-stop.test.tsx` |
| D8b | Vitest files excluded from the run that test LIVE code or a copy-lint guard | keep (why); re-enable | Agent 6 | `components`, `ws-components`, `ui-tokens`, `ws-copy`, `documents` tests (structure problem S1) |
| D9 | Unused npm dependencies (6 plus 2 dev) | remove | Agent 6 | `web/package.json`, `web/package-lock.json` |
| D10 | `scripts/deploy_to_cloud_run.sh` (manual gcloud deploy, hard-coded project and region) | remove | Agent 4 (script); lead (`DEPLOYMENT.md:67`) | `scripts/deploy_to_cloud_run.sh` |
| D11 | `docker/cloudbuild.yaml` (older Cloud Build file) | remove | Agent 5 (docker/**); lead (`DEPLOYMENT.md:85`) | `docker/cloudbuild.yaml` |
| D12 | Config files that nothing loads: `config/dev.yaml`, `staging.yaml`, `production.yaml`, `storage.yaml` | remove | Agent 4 | those four files; `docs/infrastructure-plan.md:3` (lead) |
| D13 | Tiered model router (`MODEL_ROUTER`), `config/model_routing.yaml`, `src/llm/` router | keep (why) | none | see section 2 |
| D14 | Unused methods of `account_intelligence/model_routing.py` (cost, limits, quality checks, region, retention, artifact path) | remove (the unused methods and their test) | Agent 4 | `src/account_intelligence/model_routing.py`, `tests/unit/test_model_routing.py` |
| D14b | `select_model(PipelinePhase.CHAT)` use in `chat.py:258` and the `routing:` phases of `config/models.yaml` | remove after Phases 5 to 7 | Agent 4 (Phase 6) | `chat.py:54,258`, `config/models.yaml:130-` |
| D15 | FIX_PROMPT 6.1 backend leftovers: `/v1/runs/{id}/approve`, `/v1/runs/{id}/cap*`, cap pauses, `/v1/chat/provider-key` and the BYOK store, `/v1/models` | remove after Phases 5 to 7 | Agent 4 and 6 (Phase 6) | section 2 (D15) |
| D16 | `GET /v1/runs/pull-option` | remove after Phases 5 to 7 | Agent 4 (Phase 6) | `routes/runs.py:390`, `agents/adapters.py:262` |
| D17 | Table and saved-view API with no live UI (`/v1/accounts/{d}/tables/{t}`, `/v1/views*`) | remove after Phases 5 to 7 (owner decision) | Agent 4 and 6 | section 2 (D17) |
| D18 | Root stray files (`run_sql.py`, `screenshot_all.*`, `verification-report.txt`, root `AUDIT.md`, `BACKEND_AUDIT.md`) | remove after Phases 5 to 7 (FIX_PROMPT 6.3) | lead (Phase 6) | root files |
| D19 | `src/security/pentest_gate.py::beta_gate()` | remove after Phases 5 to 7 (FIX_PROMPT 6.4) | Agent 4 (Phase 6) | `src/security/pentest_gate.py` |
| D20 | Backend routes with no live web caller that other phases need (alerts, signals, subscriptions, connectors, CRM, SSO, usage, credits, operator tenants and ops, `/tasks/*`) | keep (why) | none | section 2 (D20) |
| D21 | Python modules used only by their own tests (conformance evidence, privacy, retention, obs, rag, evalgate, CRM adapters, registers stubs) | keep (why) | none | section 2 (D21) |
| D22 | Unwired API primitives (`api/etag.py`, `idempotency.py`, `pagination.py`, `domains.py`, `pricing.py`) and duplicate facades (`agents/grading.py`, `workflow/evidence_gate.py`, `tracker/leads.py`, `agents/service.py`) | keep (why); owner decision | none | section 2 (D22) |
| D23 | `deploy/helm/web` (optional GKE chart) | keep (why); owner decision | none | `deploy/helm/web/**`, `ci.yml:20-` |
| D24 | `docs/design-review/` (210 PNG, 29 MB) | remove after D7 | lead (docs) | `docs/design-review/**` |
| D25 | `scripts/restore_from_backup.md` | remove | Agent 4 | that file |
| D26 | `web/LEGACY_TO_DELETE.txt` | remove after D4 to D8 are done | Agent 6 | that file |
| D27 | Sample and prototype content (skill `starter/`, golden synthetic accounts, hermetic fixtures, `__design` gallery, seed scripts) | keep (why) | none | section 2 (D27) |
| D28 | Retired env names in `docs/ENVIRONMENT.md` (`SLICE_SECONDS`, `OBJECT_STORE_*` ...) | keep (why) | none | section 2 (D28) |

Counts by row: **remove 15** (D1, D1w, D2, D3, D4, D4b, D5, D7, D8, D9, D10, D11, D12, D14, D25), **remove after Phases 5 to 7: 9** (D6, D14b, D15, D16, D17, D18, D19, D24, D26), **keep (why): 8** (D8b, D13, D20, D21, D22, D23, D27, D28). D1w, D4b and D8 are web halves of the same work, so the independent changes are about 12.

Rough size of the "remove" set: about 2,250 lines of Python in `src/` plus about 1,300 lines of tests
(Python), about 40 web files, 29 MB of screenshots (D24, later) and paths:
11 `/v1/files*` paths (12 operations) and `/tasks/file-process` leave ROUTE_POLICIES, OpenAPI, the isolation matrix
and the generated client.

## 2. Candidates with their proof

### D1. Older research-files upload path: remove (Agent 4; web half D1w for Agent 6)

What it is: before the `documents` service, uploaded files went through `research_files`: a chunked
upload API, a processing pipeline, a profile and analytics step, and a question-answer route per
file. The UI now uses `/v1/documents/*` (web `lib/upload.ts`, `components/ws/DocumentDrop.tsx`,
`components/ws/FileAnalysis.tsx`), and "file" research runs use `documents` (`api/routes/file_runs.py`,
`documents/graph.py`).

Proof of no use:

- Web: `grep -rn '/v1/files' web/app web/components web/lib web/ui` finds nothing. The only web
  references are `web/lib/research-files.ts`, `web/tests/unit/research-files.test.tsx` (excluded from vitest,
  `vitest.config.ts:9`; imports a file that no longer exists: knip "Unresolved imports (1)
  `@/components/ws/FileUploader`") and two pages that are `redirect("/")` stubs. `knip` also lists
  `lib/file-generator.ts` as an unused file.
- Routes: `/tmp/da/routes.py` (prefix match against live web files) gives `live=0` for all 11 paths of
  `/v1/files*`. Tests that call them are only `tests/research_files/*` and the isolation matrix.
- Imports in `src/`: a reachability pass over `src/research_files/` (roots = everything outside
  `routes/research_files.py` and the package itself) reaches only `blobstore`, `checks`, `config`,
  `detect`, `parsers/*` and `repo.log_call` from `src/documents/` (`documents/service.py:63,64,694`,
  `redact.py:27`, `sandbox_child.py:67`, `parts.py:85,99,157`). Not reachable from anything alive:
  `analytics.py` (489 lines), `modelsvc.py` (293), `pipeline.py` (400), `profile.py` (322), `qa.py`
  (110), `service.py` (58), `upload.py` (202). Only `routes/research_files.py`, `container.py:546`,
  `main.py:164-165`, `tasks.py:138-153` and the old tests import them.
- Config: `config/research_files.yaml` is still loaded (by `documents` via `research_files.config`), keep
  the file. Only its `tasks:` mapping (read by `modelsvc.py`) becomes unused.
- Scripts and Makefile: no target runs the old path; the only runner is Cloud Tasks hitting
  `/tasks/file-process` (`platform/gcp/adapters.py:129-135`) and the hermetic scheduler
  (`platform/hermetic.py:199-201`), both created only by `research_files/service.py:51`.
- Phases 5 to 7: Phase 5.8 (private retrieval) reads uploaded files through `documents`; Phase 5.7
  (privacy) works on table contents, not on this code. PLAN section 2 lists no `research_files` item.
  `src/research_files/blobstore.py` carries VERIFY V-FILES-1 and is used by `documents`: it stays.

What must change together (all by Agent 4 unless stated):

| Area | Change |
| --- | --- |
| Routes | delete `src/account_intelligence/api/routes/research_files.py` (375 lines); remove `research_files` from `routes/__init__.py:33` and `:81` |
| ROUTE_POLICIES | delete `api/policy.py:82-93` (12 `/v1/files*` entries) and the `/tasks/file-process` entry (registered in `tasks.py`) |
| Tasks | `routes/tasks.py:128-153` (`FileTask`, `file_process`) and the module docstring line; `db/owners.py:18` (`"file": "research_files"`) if nothing else uses the `file` owner kind (check `_act_as_owner_of("file")` callers first) |
| Wiring | `app/container.py:133-134` (`files` field), `:496-497` and `:647` (`container.files = file_services(...)`), `:544-548` (`file_services`); `local_blob_store` stays (documents use it); `api/main.py:158-176` (`_sweep_files`) and its call at `:206` |
| Scheduler adapters | `platform/hermetic.py:124-126` and `:199-201`, `platform/gcp/adapters.py:129-135` (`enqueue_file`); `tests/research_files/world.py:86` |
| Modules | delete the 7 modules above; cut `research_files/repo.py` down to `log_call` (plus `_j`), because `documents/service.py:694` writes embedding calls through it (see S5); `config/research_files.yaml` drop `tasks:` |
| OpenAPI | `docs/api/openapi.json`: 11 `/v1/files*` paths, `/tasks/file-process`, schemas `File*` and `FileTask`; `api/contract.py:1948-2077` (`FileUploadStarted` ... `FileState`); keep `FileAnalysisRun` at `:2242` (used by the live `/v1/accounts/{d}/file-analysis`) |
| Generated client | `web/lib/gen/api.gen.ts` loses the same operations and models (lead regenerates with `scripts/gen_web_client.py`) |
| Isolation matrix | `tests/isolation/matrix/matrix.yaml`: routes lines 451, 480-492; task lines 827, 856 (`task.file_process`); job line 872 (`file_sweep`) and `src/tenancy/catalog.py:95,115`; function entry line 309 (`research_files_orphaned`) stays because the migration function stays, but its `proof:` points at `tests/research_files/test_pipeline_db.py`, which goes: re-point it to a surviving test or keep a trimmed one. Table entries (line 125 and `config/ownership.yaml:95-100,208`) stay: the tables are not dropped |
| Single-writer guard | `tests/system/test_single_write_paths.py:85-92` (owners are `research_files/repo.py` and `pipeline.py`): update to the trimmed `repo.py` |
| Tests to delete | `tests/research_files/test_api.py`, `test_pipeline_db.py`, `test_large_file.py`, `kill_worker.py`, `large_driver.py` (about 690 lines). Keep `test_parsers.py`, `fixtures.py`, `conftest.py`, and `world.py` (its `admin` helper is imported by `tests/documents/world.py:18`; its `fixtures.pdf_bytes` by `tests/documents/fixtures.py:8`) |
| Docs (lead) | `docs/` pages naming `/v1/files` or `research_files.pipeline` (`grep -rln 'research_files' docs`), `docs/WIRING.md`, `docs/architecture/loops.md` (regenerated) |
| Database | do NOT drop the `research_file*` tables or `research_files_orphaned`: they hold data (FIX_PROMPT rule 7). A drop is an owner decision |

D1w, web (Agent 6): delete `web/app/(client)/research-files/page.tsx` and `[file_id]/page.tsx` (4-line redirect
stubs), `web/lib/research-files.ts` (109 lines), `web/lib/file-generator.ts` (353 lines; imported by nothing, no
Python or web caller), `web/tests/unit/research-files.test.tsx`; remove their entries from `web/tsconfig.json:77,78,88`,
`web/eslint.config.mjs:7` and `web/vitest.config.ts:9`.

### D2. "Unavailable operator routes" in `web/proxy.ts`: remove (Agent 6)

`web/proxy.ts:34-44` returns a production 404 for `/operator/announcements`, `/operator/audit`,
`/operator/monitors`, `/operator/review`. `find web/app/operator -name page.tsx` shows none of these four
pages exists (`(console)` holds clients, configuration, cost, defaults, failures, library, runs, spend,
support, tenants, users, view), so Next already answers 404 for them. `grep -rn 'operator/(announcements|monitors|review|audit)'`
finds the paths only in `proxy.ts` and in docs (`docs/UI_REMOVED.md:10-13`, `docs/UI_BACKEND_AUDIT.md:145-148`,
`docs/frontend/operator-route-map.md:16-19`, which describe the pages as removed or "hidden in
production"). No test names them. Change: delete lines 34-39 (the Set), 40 and 42-44 so that `response`
is `NextResponse.next({ request: { headers: requestHeaders } })`. The CSP code is untouched. Docs (lead)
should stop saying the routes are "hidden in production".

### D3. Tracked probe scripts in `web/`: remove (Agent 6)

`git ls-files web | grep '^web/\.'` shows `.csp.mjs`, `.gal.mjs`, `.probe.mjs`, `.shots.mjs` tracked (11, 17, ~10,
23 lines). `grep -rIl` finds them only in `docs/deploy/PLAN.md` and `docs/build/*`; no package script, workflow
or doc runs them. `.probe.mjs` is the only eslint warning (`npx eslint .`: 0 errors, 1 warning, `'tall'
assigned but never used`). `.probe.mjs`, `.csp.mjs`, `.shots.mjs` also embed a hard-coded dev login (S3). Keep
`web/scripts/shots.mjs` and `web/scripts/phase4b-shots.mjs` (the owner's local-web-dev workflow and PLAN section 1 name them).

### D4. Dead web components, libs and operator-console files: remove (Agent 6)

Proof: `npx --yes knip --no-config-hints` (run from `web/`, result 51 unused files, 6 unused dependencies,
325 unused exports) plus `grep -rlE 'from ".../<name>"'` over `app components lib ui tests e2e proxy.ts`
for each file below: nothing outside the file list itself, or only the excluded unit tests, imports them.
All are on the tsconfig, eslint and vitest exclude lists already, so they are out of the build. FIX_PROMPT 6.1
says "dead components" in general; these are proven dead and self-contained, so wave 2 may act now.

Delete (paths under `web/`):

- `components/ws/`: `AccountAsk`, `AccountHeader`, `AccountList`, `AccountRows`, `AccountTabs`, `Ask`,
  `CitationChip`, `CitedText`, `Conversations`, `DataGrid`, `DeepDiveLinks`, `Downloads`, `EvidenceDrawer`,
  `evidence.ts`, `HomeAsk`, `InternalView`, `RefreshDialog`, `RunStatus`, `RunStopReport`, `SettingsFrame`,
  `SettingsNav`, `TableScreen`, `UsedPanel`, `WebOffer` (the live twins are `components/ask/*` and
  `components/accounts/*`; `components/ws/Ask.tsx` and `components/accounts/AccountAsk.tsx` are the same-named duplicates, S6).
  Keep `Composer`, `Conversation`, `DocumentDrop`, `FileAnalysis`, `GradeMark`, `RunSteps`, `WorkspaceShell` (live).
- `ui/`: `ConfirmButton`, `DataTable`, `Feedback`, `Input`, `RunProgress`, `Scene`, `SignedIn`, `ThemeToggle`,
  `theme.ts`, `theme-server.ts`, `contrast.ts` (`contrast.ts` is imported only by the excluded `ui-tokens.test.ts`, see D8b: delete it
  only if that test is rewritten).
- `lib/`: `client.ts`, `operator-render.ts`, `report.ts`, `run-stop.ts`, `schedule.ts`, `api/denylist.ts`, `api/errors.ts`
  (`lib/file-generator.ts`, `lib/research-files.ts` are in D1w, `lib/provider-labels.ts` in D4b).
- `app/operator/(console)/`: `OperatorCrumb.tsx`, `OperatorStates.tsx`, `SupportGrantLookup.tsx`.
- Dead exports inside live files (`knip` "Unused exports", 325 entries, 305 of them in `lib/gen/api.gen.ts`, which is the lead's):
  `components/ws/RunSteps.tsx` `Checkpoint` (113), `StepList`, `TERMINAL`; `lib/operator-api.ts:97`
  `operatorMethodIsSafe`; `lib/query.ts` `LIMITS`, `needsValue`; `lib/sse.ts` `parseFrame`; `ui/primitives.tsx`
  `cx`, `EmptyState`, `LoadError`, `Field`; `ui/design.tsx` `gradeLabel`; `lib/ws-format.ts` `day`, `runState`. `Checkpoint` in
  `RunSteps.tsx:113-162` is FIX_PROMPT 6.1 ("RunSteps.tsx:144-162"): leave to Phase 6.
- Keep: `components/ProviderKey.tsx` (D15), `app/%5F%5Fdesign/gallery.tsx` and `page.dev.tsx` (D27).

Everything that must change with it: remove the same paths from `web/tsconfig.json` (lines ~70-95), `web/eslint.config.mjs:7`
and `web/vitest.config.ts:9`; `docs/WIRING.md` (lead regenerates; it lists calls from these dead files, for example
`web/components/ws/AccountList.tsx`, `ws/Ask.tsx`, `ws/Conversations.tsx`). No backend change.

### D4b. Operator providers route and lib: remove (Agent 6)

`web/app/api/operator/providers/route.ts` (serves vendor names and credential variable names to a staff session) has a single caller:
`web/lib/provider-labels.ts:16`, which knip lists as an unused file. No test, no page (`grep -rn "operator/providers" app lib components ui tests e2e`).
Delete both. `docs/WIRING.md` is regenerated.

### D5. Redirect stubs not named in FIX_PROMPT 6.1: remove (Agent 6)

Each is a 4-line `redirect(...)` page with the comment "Removed from the product". Found by listing app files under 12 lines that
contain `redirect(` and checking for inbound links (`grep -rn '"/audit'` and similar in `app components lib ui e2e/replica proxy.ts`):
the only inbound references are the dead `components/ws/SettingsNav.tsx` (D4) and the excluded unit tests.

Delete: `web/app/(client)/accounts/[domain]/history/page.tsx`, `web/app/(client)/accounts/[domain]/tables/[table]/page.tsx`,
`web/app/(client)/audit/{page,layout}.tsx`, `web/app/(client)/feedback/{page,layout}.tsx`,
`web/app/operator/(console)/configuration/page.tsx`, `web/app/operator/(console)/failures/page.tsx`, and the research-files stubs (D1w).
The matching backend routes are not removed here (D20, D17). No catalog, OpenAPI or client change.

### D6. Redirect stubs named in FIX_PROMPT 6.1: remove after Phases 5 to 7 (Phase 6)

`app/(client)/alerts`, `chat`, `downloads`, `runs`, `usage` (+`layout.tsx`), `settings/{alerts,connectors,crm,model-key,security,sso,support,users}` and
`settings/page.tsx` (+`layout.tsx`), `app/operator/(console)/{spend,cost,tenants,support}`. They are proven dead (stubs, no inbound links
from live code), but Phase 6.1 lists them with the catalog, OpenAPI and client changes, so they wait. `chat` and `runs/[run_id]/page.tsx`
(not a stub) are separate: `runs/[run_id]` is live.

### D7. Excluded Playwright suites, configs and shot scripts: remove (Agent 6)

`web/e2e/parity/*` (7 files), `web/e2e/review/screens.spec.ts`, `web/playwright.parity.config.ts`, `web/playwright.review.config.ts`,
`web/screenshot_test.spec.ts`: knip lists them unused; `web/tsconfig.json`, `eslint.config.mjs` exclude them; CI runs
`playwright.config.ts` and (Phase 4B) `playwright.replica.config.ts`, never these (`grep -n "parity\|review" .github/workflows/ci.yml`: none).
They are on `LEGACY_TO_DELETE.txt`. Also unreferenced by any package script or workflow: `web/scripts/route-js.mjs`, `web/scripts/replica-shots.mjs`;
`web/scripts/visual-shots.mjs` is cited only by `docs/build/VISUAL_NOTES.md` (lead; update that note). Keep `e2e/helpers.ts`, `tenant.spec.ts`,
`run-download.spec.ts`, `e2e/replica/screens.spec.ts`, `scripts/phase4b-shots.mjs`, `scripts/shots.mjs`, `scripts/copy-standalone-assets.mjs`
(`package.json` postbuild).
Docs to adjust (lead): `docs/SCREEN_MATRIX.md`, `docs/UI_BACKEND_AUDIT.md` mention the parity suite.

### D8 and D8b. Vitest files excluded from the run

`web/vitest.config.ts:9` excludes 7 test files, so they never run (and `npm test` is green without them).

- Remove: `tests/unit/research-files.test.tsx` (tests D1w code, imports a missing component) and
  `tests/unit/run-stop.test.tsx` (tests `lib/run-stop.ts` and `components/ws/RunStopReport.tsx`, both dead).
- Keep and re-enable, they test live code or a guard: `documents.test.tsx` (`lib/documents`, `DocumentDrop`, both live),
  `ws-copy.test.ts` (copy lint over `lib/messages.ts` and the generated denylist: a client-output check that is
  silently off, see S1), `components.test.tsx` (`Meter`, `GradeMark`, `useApi` are live; its `RunProgress`, `DataTable`, `Input`
  imports are dead), `ws-components.test.tsx` (`Composer`, `Conversation`, `WorkspaceShell` live; `CitedText`,
  `EvidenceDrawer`, `UsedPanel`, `WebOffer` dead), `ui-tokens.test.ts` (reads the CSS tokens, uses `ui/contrast.ts`).
  Agent 6 splits the live assertions from the dead ones and removes the file from the exclude list; deleting the dead components (D4)
  must not delete the live assertions.

### D9. Unused npm dependencies: remove (Agent 6)

`knip`: `@radix-ui/react-popover`, `@radix-ui/react-slot`, `@radix-ui/react-tooltip`, `@tanstack/react-table`, `clsx`,
`tailwind-merge` (dependencies), `postcss`, `tsx` (devDependencies). Cross-check: `grep -rn 'clsx\|tailwind-merge\|@tanstack\|react-tooltip\|react-popover\|react-slot'
app components lib ui tests` finds no import; there is no `postcss.config.*` or `tailwind.config.*` (Next bundles its own postcss); no script
runs `tsx`. `@radix-ui/react-dialog` stays (live). Change `web/package.json:18-22,29,43-44` and regenerate `web/package-lock.json`
(offline lockfile edit with `npm install --package-lock-only` in the worktree, never into the main repository). Do not run `npm install` in `/home/sakib/abm-replica`.

### D10. `scripts/deploy_to_cloud_run.sh`: remove (Agent 4)

198 lines of `gcloud run deploy` / `jobs create` / `services update` with `PROJECT_ID` defaulting to a literal project (`abmgenerator`,
line 7) and `REGION` defaulting to `asia-south1` (line 8). Deployment is Terraform plus `scripts/deploy_gcp.sh` (workflow-driven, WIF);
`Makefile:136-138 deploy-staging` only prints commands. Only `DEPLOYMENT.md:67` (root file, lead) references it. Not in any workflow,
Makefile target or test (`grep -rIl deploy_to_cloud_run .`). Under the owner's money rule a script that creates Cloud Run resources outside the
IaC is a risk, not an asset. Lead updates `DEPLOYMENT.md` (or retires it with the other root docs).

### D11. `docker/cloudbuild.yaml`: remove (Agent 5)

Older Cloud Build pipeline (`_REGION: 'asia-south1'`, line 47); PLAN section 1 already calls it "an older Cloud Build file; the repository's CI is
GitHub Actions". Referenced only by `DEPLOYMENT.md:85` (`gcloud builds submit`) and `docs/deploy/PLAN.md`. No workflow, Makefile target or Terraform
uses it (`grep -rIl cloudbuild . --exclude-dir=.git`).

### D12. Configs nothing loads: remove (Agent 4)

`config/dev.yaml`, `config/staging.yaml`, `config/production.yaml` (identical `slice_seconds`/`max_slices` overrides) and `config/storage.yaml`
(`raw_payloads.backend: local`). `grep -rn '(dev|staging|production|storage)\.yaml' src scripts tests Makefile infra docker .github compose*.yml`
finds no loader; the only reader is `config_files.py:137`, which hashes `CONFIG_DIR.glob("*.yaml")` (the hash dictionary
changes, nothing reads these values). `slice_seconds`/`max_slices` come from `config/workflow_v1.yaml` (`container.py:384-393`), exactly as
`docs/ENVIRONMENT.md:142-143` says. The only doc mention is `docs/infrastructure-plan.md:3` (lead should fix). Not removed: `pricing.yaml`,
`claims.yaml`, `messaging_rules.yaml`, `risk_register.yaml`, `secrets_rotation.yaml`, `soc2_controls.yaml`: the section 11 conformance spec cites
them as evidence (`config/section11_spec.yaml:42,50,55,64-67`), so deleting them would weaken `scripts/check_conformance.py` (D21).

### D13. Tiered model router, `MODEL_ROUTER` and `config/model_routing.yaml`: keep (why)

`MODEL_ROUTER=on` is the only switch (`container.py:612-619`) and it refuses to start while `benchmark_report` is null (`container.py:313-316`;
`config/model_routing.yaml:10` is null). Nothing sets the variable: no compose file, Terraform file, workflow or `.env.example` mentions it
(`grep -rn MODEL_ROUTER . --exclude-dir=node_modules --exclude-dir=.git`: only code, docs and `scripts/check_routing_governance.py`).
So today it is a switch that cannot be turned on. Why it still stays:

- `config/model_routing.yaml` is loaded on every model call, not only by the router: `llm/tasks.py` takes task kinds, tiers and token limits
  from it, `research_files/config.py` and `research_input_modes.yaml` name its task kinds, and `config/telemetry/metrics.yaml:17-18` takes label
  bounds from it. Removing the file breaks the default path.
- The router, health, call record and `with_model_split` are the intended implementation of the "Model split" setting (FIX_PROMPT 4.9 A7)
  and are governed by ADR-0042, ADR-0045 and `scripts/check_routing_governance.py` (CI static job, `eval.yml:36`). Deleting them is a decision
  for the owner, not a cleanup. Open point for Agent 4: `with_model_split(cfg, getattr(deps, "model_split", "split"))`
  (`container.py:322`) reads an attribute that no `Deps` class defines (`grep -rn model_split src`: only `llm/config.py` and this line), so the A7 choice never
  reaches the router; either wire it or drop the `getattr`.

### D14. Unused parts of `src/account_intelligence/model_routing.py`: remove (Agent 4); D14b later

A second, older "Phase 5" router, 354 lines. The only production use is `routes/chat.py:54,258`
(`get_model_router().select_model(PipelinePhase.CHAT)`). `grep -rn` for `calculate_cost`, `check_limits`, `execute_quality_checks`,
`handle_phase_failure`, `log_call_usage`, `cached_prefix_block`, `raw_artifact_path`, `get_inference_region`, `workspace_retention_days` over `src` finds
no caller outside the file; `tests/unit/test_model_routing.py:48-72` is the only user. vulture agrees (`QualityCheckFailed`, nine entries at 60%).
`get_inference_region` (lines 327-338) returns `"us-central1"` in both branches, a hard-coded region with a dead condition (S4).
Remove: everything except what `select_model`, `validate_model_id` and `get_model_router` need, plus the matching tests in
`tests/unit/test_model_routing.py` and the `PhaseFailureAction`/`QualityCheckResult`/`ModelCallUsage` types that become unused. D14b: the
`routing:` phases in `config/models.yaml` and the `chat.py` call are the "phase model flags" of FIX_PROMPT 6.1, so they go with Phase 6.

### D15. FIX_PROMPT 6.1 backend leftovers: remove after Phases 5 to 7 (Agent 4 and 6, Phase 6)

Listed, not to be touched now. Proof that they are dead for the replica web (live-web grep, `/tmp/da/routes.txt`):

| Item | Where | Proof |
| --- | --- | --- |
| `POST /v1/runs/{run_id}/approve` | `routes/runs.py:628`, `policy.py:111` | web caller only in `components/ws/RunSteps.tsx` (`Checkpoint`, FIX_PROMPT 6.1) |
| `GET /v1/runs/{id}/cap`, `POST .../cap/approve`, `/cap/reject`, `/cap/extend` | `routes/spend.py:69-174`, `policy.py:115-118` | no live web caller |
| `POST .../cap/pauses/{request_id}/approve`, `/decline` | `routes/spend_v2.py:121,176`, `policy.py:123-124` | no live web caller; `tasks.py` `/tasks/approval-timeout` also resumes cap pauses (`expire_cap_pause`), so Phase 6 must change both |
| `GET/PUT/DELETE /v1/chat/provider-key` and the BYOK store | `routes/chat.py:61-65,102-160,316-330`, `chat/credentials.py`, `api/main.py:92-94`, `config.py:243` (`BYOK_KMS_KEY`), `contract.py:1931,1939`, `infra/stack/main.tf` (mentions BYOK), `tests/unit/test_chat_credentials.py` | only caller `web/components/ProviderKey.tsx` (knip: unused file, on the legacy list); but the chat turn still reads the tenant key (`chat.py:316`), so removal is more than a route |
| `GET /v1/models` | `routes/chat.py:58,118` | web uses `/v1/models/choices` (`ModelMenu.tsx:15`), never `/v1/models`; matrix line 522 |
| `components/ProviderKey.tsx`, phase model flags, the admin Settings page text | web | knip unused |

Each removal changes: ROUTE_POLICIES (or `register(...)` calls), the route file, `docs/api/openapi.json`, `web/lib/gen/api.gen.ts`, `tests/isolation/matrix/matrix.yaml`
(`runs/.../cap*` and friends), and the tests named above.

### D16. `GET /v1/runs/pull-option`: remove after Phases 5 to 7 (Agent 4)

No web file mentions `pull-option`; tests: `tests/phase3/test_abm_modes_api.py` and the matrix only. It reports whether a "Phase 2 only" pull is allowed
(`routes/runs.py:390-398`, `agents/adapters.py:262`), a leftover of the three-phase model that the replica scopes (jobs, people, full, topic) replaced.
Proof is strong, but the Phase 5.11 graph work may reuse `pull_option`; decide after it.

### D17. Table and saved-view API without a live UI: remove after Phases 5 to 7 (owner decision)

`GET /v1/accounts/{domain}/tables/{table}` and `/v1/views*` (4 operations) are called only by dead `TableScreen`/`DataGrid` (D4) and `lib/query.ts:86`.
The replica shows sheets (`/v1/accounts/{d}/sheets/{sheet}`) instead. Tests: `tests/phase3/test_ws4_views.py`, `tests/system/test_fake_model_scenarios.py` (calls
the table route). `src/views/` is not removable: `views/packages.py`, `views/rowsources` feed the exports. Remove only the routes, `views/saved.py` and
`views/query.py` if nothing else uses them (check `grep -rn "views.saved\|views.query"`), with the usual catalog, OpenAPI, client, matrix and test changes. Waits for the
owner to confirm saved views are gone for good.

### D18, D19. Root strays and `beta_gate()`: remove after Phases 5 to 7 (FIX_PROMPT 6.3, 6.4)

Listed for completeness, Phase 6 owns them. `run_sql.py` runs `scripts/check_rls_catalog.sql` through `ADMIN_DSN` (cited by `docs/RELEASE_REPORT.md` and BUILD_PROMPT only);
`screenshot_all.js`, `.mjs`, `.spec.ts` (no references; the first two embed the dev login, S3); `verification-report.txt` (cited by BUILD_PROMPT, mentions `helm`);
`AUDIT.md`, `BACKEND_AUDIT.md`. `src/security/pentest_gate.py::beta_gate()` is used only by `tests/crosscut/test_x5_security.py` and conformance evidence (`SC.soc2_pentest`); Phase 6.4 decides.

### D20. Routes with no live web caller that stay: keep (why)

From `/tmp/da/routes.txt` (`live=0`), grouped. All carry ROUTE_POLICIES, isolation-matrix and tests; the reason each stays:

| Group | Routes | Why it stays |
| --- | --- | --- |
| Alerts and signals | `/v1/alerts*`, `/v1/signals`, `/v1/subscriptions*`, `/v1/monitoring/budget` | Phase 5.3 (alerts engine, outbox, digest) and 5.2 (deltas) wire to them; in-app notifications (`/v1/notifications`) are live |
| Credits and usage | `/v1/credits`, `/v1/usage`, `/v1/usage/export`, `/v1/usefulness*`, `/v1/feedback` | `/v1/credits` serves the replica pool (FIX_PROMPT 2.3 single credit truth, 4.4 and 4.5 estimates read it); usage and usefulness feed the admin console KPIs and the pilot loop. `/v1/feedback` is posted by the dead `ui/Feedback.tsx` only: owner decision whether the feedback loop returns |
| Connectors, CRM, SSO, auth | `/v1/connectors*`, `/v1/crm/{crm}/*`, `/v1/sso/*`, `/v1/auth/password/enroll`, `/v1/mail-and-drive/callback` | OAuth callbacks and server-to-server entries are not called from the page; SSO is the sign-in path (FIX_PROMPT 4.11 "every sign-in method"); mail and drive per-user rework (4.2) uses the callback |
| Settings and branding | `/v1/settings`, `/v1/settings/branding*`, `/v1/branding`, `/v1/documents/policy`, `/v1/documents/estimate` | read by `documents` and `views` code paths and tests (`tests/documents/test_file_runs.py`); `/v1/settings` replaced by `/v1/me/limits` in the UI, so Phase 6.1 "Replace the current Settings page" may retire the old PUT |
| Operator | `/operator/v1/flags`, `/keys`, `/ops/cost`, `/ops/failures`, `/ops/stuck`, `/pilot/aggregate`, `/tenants/*` (suspend, reactivate, plan, quotas, deletion dry-run, approve, execute), `/view/accounts`, `/view/settings` | `docs/runbooks/tenant-suspended.md:19` and `deletion-request.md:21` call tenants and deletion; Phase 5.7 (privacy) and 5.10 (offboarding) extend them; ops routes carry the guarantee `test_ops_routes_are_operator_only_and_carry_no_content` (O15); no web page, so a console page or removal is an owner decision |
| Tasks | `/tasks/*` (except `file-process`, D1) | PLAN section 2, Phase 5.1: never removed |

### D21. Python modules imported only by their own tests: keep (why)

An import-graph pass (`/tmp/da` scratch script) lists about 60 `src/` modules that no other `src/` module imports. Cross-checked against
`config/section11_spec.yaml` and PLAN section 2: they are the evidence the conformance check (`scripts/check_conformance.py`, needs `ADMIN_DSN`, not run here) cites,
or Phase 5 code. Examples and the reason:

- `privacy.{dsar,minimise,retention}`, `alerts.{digest,outbox}`, `refresh.{probe,summary_diff,sourcecodes}`, `obs.*`, `account_intelligence/rag/{graph,metrics,synthetic}`:
  Phase 5.7, 5.3, 5.2, 5.5, 5.8 (PLAN section 2 names them).
- `registers.{claims,decisions,messaging,risks}` (6-line `pass` stubs), `metrics.{report,linkcheck}`, `quality.{analyst_loop,regression,release_check}`,
  `security.{rotation,secrets_scan,soc2,pentest_gate}`, `unitcost.{anomaly,rollup}`, `governance.account_cost`, `datamodel.counts`, `contracts.monitor`:
  `config/section11_spec.yaml:35-67` lists them as `symbol:` evidence for the section 11 requirements. Deleting them breaks that check without replacing the requirement.
  The stubs `registers/*` are honest about being unimplemented ("the repository check remains unimplemented"); they are a decision item for the owner, not dead code.
- `crm_writeback.{hubspot,salesforce}`, `connectors_client.{crm_hubspot,crm_salesforce,gmail,notes}`, `evalgate.{cli,store,calibrate,adversarial}`,
  `people.{docs,erasure}`, `exports.{docs,fingerprint}`, `parallel.simulate`, `verify.{figures,narrative}`, `account_intelligence.connectors.registry_internal`:
  adapter and tooling code with tests and, for `evalgate.cli`, a workflow step (`eval.yml`).
- One stale pointer to fix (Agent 4): `config/section11_spec.yaml:35` cites `{file: src/gate/pipeline.py}`, which is not in the tree (FIX_PROMPT 3.6 already deleted `src/gate/`).

vulture at the project's own CI setting (`Makefile:104`: `vulture src scripts/vulture_whitelist.py --min-confidence 80`) reports 0 findings; at 60% it reports
1,025 lines of which about 205 are unused functions or classes outside route handlers and `ports.py`, almost all in the groups above.

### D22. Unwired API primitives and duplicate facades: keep (why), owner decision

`api/etag.py`, `api/idempotency.py`, `api/pagination.py`, `api/domains.py`, `api/pricing.py` are not imported by any route (only
`tests/api/test_api_guarantees.py`, `test_problem.py`), but each carries a `GUARANTEE(...)` tag that `scripts/check_guarantees.py` ties to a real test; removing
a module means removing the guarantee, which is "weakening an existing check". The real defect is that idempotency, ETag and pagination are not applied
(no route imports `api/idempotency.py`); that is a hardening item for Agent 1 and the owner, not a cleanup. The facades `account_intelligence/agents/grading.py`
(legacy wrapper over `grading/`), `workflow/evidence_gate.py` (97 lines, "delegates to grading.spans"), `tracker/leads.py` and `agents/service.py` (`AbmRunService`,
only `tests/agents/conftest.py`) are production-unused duplicates of logic that has one real home in `grading/`; removing them is safe after Phase 5.11 (research graph
shape) confirms the graph does not import them. Treat as "remove after Phases 5 to 7" if the owner wants the cleanup.

### D23. `deploy/helm/web`: keep (why), owner decision

`RUNBOOK.md:3` calls it an "optional web-only Helm chart for a separately operated GKE cluster"; `ci.yml:20-33` lints and templates it with `alpine/helm`. The platform targets
Cloud Run (PLAN section 1), so the chart is a second deploy path that nothing in the plan uses. It is not dead by the five tests (CI runs it), so it stays until the owner
retires GKE as an option; Agent 5 owns the CI job.

### D24 to D26. Later cleanup

- D24: `docs/design-review/` holds 210 PNG files (29 MB); referenced only by the excluded `web/e2e/review/screens.spec.ts`, `web/playwright.review.config.ts` (D7) and
  `docs/SCREEN_MATRIX.md`. Remove with D7 (lead owns `docs/`).
- D25: `scripts/restore_from_backup.md`: one-off note about a 2026-10-08 zip archive outside the repository; no reference anywhere (`grep -rIl restore_from_backup`). The
  restore runbook is `docs/runbooks/restore.md`. Remove (Agent 4).
- D26: `web/LEGACY_TO_DELETE.txt`: its second half lists live files with a `web/` prefix (login page, `healthz`, operator callback routes); do not run its delete command as written.
  Remove the file once D4 to D8 are done.

### D27. Sample and prototype content: keep (why)

`.claude/skills/*/assets/starter/**` and `fixtures/*.sql` (the skill's own starter and lint fixtures, used by `scripts/check.sh` and `selftest.sh`, `Makefile` `static`);
`evals/golden/**` and `src/evalgate/golden.py` (synthetic golden accounts, used by `evals/` and `eval.yml`); `config/hermetic/**` (loaded by directory by `LLM_FAKE=hermetic` and the
hermetic providers); `scripts/seed_replica_demo.py`, `seed_pilot.py` (Makefile `seed`, replica stack); `web/app/%5F%5Fdesign/{gallery,page.dev}.tsx` (dev-only gallery: `next.config.ts:12-14` gives `.dev.tsx` pages
only in development, which is why knip reports them unused). No prototype directory exists in the tree. The only prototype-like dead content is D3, D7 and D24.

### D28. Retired env names: keep (why)

`docs/ENVIRONMENT.md:137-149` is generated from `scripts/gen_env_docs.py:59-66`. Those names are documented as "never read" on purpose so that a stale local `.env` can be cleaned.
`grep` confirms no `src/` code reads them. Nothing to remove.

## 3. Structure problems

Owner is the agent who would fix it; "lead" for docs and generated files.

| # | Problem | Where | Owner |
| --- | --- | --- | --- |
| S1 | Seven vitest files are excluded from the run, so a copy-lint guard and the documents UI tests never run | `web/vitest.config.ts:9` (see D8b); same list in `eslint.config.mjs:7` and `tsconfig.json:87-93` | Agent 6 |
| S2 | Business logic in React components and pages: percentage and bar-height maths in the operator overview; an elapsed-time and duration formatter copied four times (`components/ws/RunStatus.tsx:12`, `components/ws/FileAnalysis.tsx:29-34`, `components/accounts/ResearchCompany.tsx:16`, `components/accounts/RefreshForm.tsx:23`); the same non-negative integer clamp in four operator pages (`defaults/page.tsx:59`, `RequestsList.tsx:23`, `users/page.tsx:54`, `clients/[tenantId]/page.tsx:96`); stage-progress maths in `components/ws/RunSteps.tsx:37` | `web/app/operator/(console)/page.tsx:19,39,73` and the files named | Agent 6 (move maths to the API as ready values or one `lib/ws-format.ts` helper) |
| S3 | Hard-coded dev login (email and password) in tracked scripts | `web/.probe.mjs`, `web/.csp.mjs`, `web/.shots.mjs`, `screenshot_all.js`, `screenshot_all.mjs`; also `README.md` | Agent 6 (web), lead (root) via D3 and D18; no value is repeated here |
| S4 | Hard-coded regions, project and URLs: `src/account_intelligence/model_routing.py:337-338` (`us-central1`, same in both branches); `scripts/deploy_to_cloud_run.sh:7-8` (project `abmgenerator`, region `asia-south1`); `docker/cloudbuild.yaml:47`; `scripts/deploy_staging.sh:9,23-30` (`asia-south1` in the printed commands); `src/llm/adapters/moonshot_api.py:51` (`https://api.moonshot.ai/v1`); `src/connectors_client/maildrive.py:193,233,268,295` (Gmail, Graph, Drive base URLs); `src/account_intelligence/api/operator.py:81-82` and `web/app/api/operator/login/route.ts:28`, `callback/route.ts:92,108` (Google issuer, token and auth URLs). Model ids are not in code (only in config and one docstring), good | files named | Agent 4 (src, scripts), Agent 6 (web), Agent 5 (cloudbuild, D11) |
| S5 | `documents/service.py:694-705` logs each embedding batch into `research_file_model_calls` through `research_files.repo.log_call`, with the document id in the `file_id` column. The table has no FK (`0051_research_files.sql:168-184`), but its owner trigger and RLS owner policy were built for `research_files` parents (`0063_private_workspaces.sql:1314-`), so the document owner may not be recorded. Dropping D1 makes this coupling the only user of that table | `src/documents/service.py:694`; `src/research_files/repo.py:516` | Agent 4 (decide: own `document_model_calls` table via migration 0070+, or keep and test the owner), Agent 1 for the RLS view |
| S6 | Same-named duplicate components in two folders: `components/ws/Ask.tsx` and `components/ask/Ask.tsx`; `components/ws/AccountAsk.tsx` and `components/accounts/AccountAsk.tsx`; `components/ws/RunStatus.tsx` etc. Two Python `agents` packages: `src/agents/` and `src/account_intelligence/agents/` (prompts, output, grading facade). `spend_v2.py` is a version-suffixed route module | as named | Agent 6 (web via D4); Agent 4 for the Python names (rename after Phase 5.11, because imports are many) |
| S7 | API origin default duplicated in five web files with two different values (`http://127.0.0.1:8080` and, in production, `http://api:8080`) | `web/app/readyz/route.ts:7`, `web/app/v1/[[...path]]/route.ts:36`, `web/lib/operator-oidc.ts:25`, `web/lib/server-api.ts:15`, `web/app/api/auth/logout/route.ts:14` | Agent 6 (one `apiOrigin()` in `lib/server-api.ts`; the production fallback to a compose service name should fail closed instead) |
| S8 | SQL and business rules inside route handlers (raw `text(...)` statements): `routes/auth.py` (17 statements, lines 145-570), `routes/runs.py` (15, lines 239-788), `routes/admin_console.py` (14, lines 95-676), `routes/accounts.py` (7, lines 77-331), `routes/research.py` (5, lines 214-320), `routes/account_workspace.py` (4, lines 82-223), `routes/tasks.py:196,286`, `routes/credits.py:70,75,244`, `routes/spend_v2.py:91,316`. Repo modules exist for most of these domains | files and lines named | Agent 4 (move into repos or services; behaviour-neutral, tests exist; do after the removals so it is not done twice) |
| S9 | Exception handlers without any reason comment (28 `except Exception` or `BaseException` with no comment within two lines); five of them swallow with no log: `src/obs/setup.py:59` (documented in its docstring, counts a drop), `src/account_intelligence/rag/prompt.py:125`, `src/account_intelligence/replica/library.py:159`, `src/verify/narrative.py:81`, `src/account_intelligence/api/routes/chat.py:414` (re-raises as an SSE error event). No bare `except:` exists. The rest of the list is in the agent log | files named | Agent 4 (add one-line reasons; `library.py:159` and `narrative.py:81` should record why a failure is treated as empty or "unsupported") |
| S10 | Duplicate domain and host normalisers: `tenancy/settings.py:83` `normalise_domain`, `connectors/models.py:26` `_domain`, `api/domains.py:35` `validate_domain` (unused outside tests), `routes/runs.py:398` and `agents/setup/agent.py:50` and `agents/service.py:50` (`strip().lower().rstrip(".")` inline), plus eight `rstrip(".")` host helpers (`connectors/security.py:60,81,86,138`, `grading/spans.py:82`, `grading/lineage.py:34,70`, `urls.py:56`) | files named | Agent 4 (one shared helper in `urls.py` or `tenancy`, after Phase 5) |
| S11 | Naming outliers: test folders named by build phase (`tests/phase3`, `tests/phase4`), `src/opendoor` package, `routes/spend_v2.py`, two `model_routing` modules (`src/account_intelligence/model_routing.py` and `config/model_routing.yaml` with `src/llm/`), `ws` as a component folder name, root-level `docs/deploy` vs `deploy/helm` | as named | lead / Agent 4: record only; renames risk import churn and are not part of this phase |
| S12 | `config/section11_spec.yaml:35` cites `src/gate/pipeline.py`, which does not exist | `config/section11_spec.yaml:35` | Agent 4 (D21) |
| S13 | `web/LEGACY_TO_DELETE.txt` mixes dead paths and live `web/`-prefixed paths; its documented `git rm` command is unsafe to trust | `web/LEGACY_TO_DELETE.txt` | Agent 6 (D26) |

## 4. What this report could not prove

- `scripts/check_conformance.py`, `check_route_catalog.py` and the route and isolation tests were not run (no test database, read only). Whether removing D1 leaves the catalog and
  matrix checks green must be confirmed by Agent 4 in its own test database.
- The live-web filter uses `knip` without a project config file (the default Next.js plugin); it treats files imported only by tests as used. It does not see dynamic imports
  by string. Agent 6 should rerun `npx --yes knip` after each deletion batch.
- `vulture` output is heuristic (decorated route handlers, pydantic fields and ports show as unused); only module-level reachability and exact greps were used as proof.
- Documentation mentions were searched by file name; docs that describe a feature without naming a path (for example the user guide) were not read.
