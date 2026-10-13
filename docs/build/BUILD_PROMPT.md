# Build prompt: Account Intelligence, final product (frontend + backend), minimal UI

You are working in the existing Account Intelligence repository at `/home/sakib/ABM Generator` (Next.js 16 web app in `web/`, FastAPI API and worker in `src/`, PostgreSQL with forced row level security, Redis, object storage, local stack in `compose.yml`). Build the product described below. Do not start a parallel codebase. Reuse the existing run pipeline, paid-call ledger, grading code, route catalog, audit classes and RLS conventions.

This prompt was written after a read-only audit of the repository on 2026-10-13. Section A records what that audit found, with file paths, so you start from facts. Verify each fact before you rely on it; the tree was moving when it was written.

---

## A. Starting state (audit findings, verify before relying on them)

### A1. Preconditions you must check first (blockers)

1. **The ABM research skill package is not on this machine.** No `SKILL.md` with `references/checklist.md`, `phase1-pull.md`, `phase2-build.md`, `tools.md`, `signals.md`, `output-spec.md`, `lessons.md` exists in the repo or on disk. `docs/abm/SOURCE_MAP.md` says "Archive status: absent"; `docs/abm/LESSONS.md` is marked INCOMPLETE and `config/lessons.yaml` is empty. The two skills in `.claude/skills/` (`account-intelligence-platform`, `production-deployment-consolidator`) are skills for *building* this platform, not the research skill. The only description of the research skill on disk is `ABM_Skill_Readers_Guide_v2.docx` in the repo root (guide to skill v1.8, a 16-file zip).
   - Expected location once supplied: `skills/abm-account-research/` (extracted zip).
   - If it is not there when you start: do section 0 and every part of the build that does not need the skill text (registry, executor shell, meter, checkpoints, chat modes, file mode, frontend, deletions, tests with a small fixture skill under `tests/fixtures/skills/`). Mark acceptance tests 1 and 4 (skill version part) as blocked on the package. Never invent the skill's content from the reader's guide.
2. **Keys.** In `.env`, `ANTHROPIC_API_KEY`, `P_SCRAPE_API_KEY` (Firecrawl), `P_PEOPLE_API_KEY` (BitScale), `P_ORG_API_KEY` (Sumble) and `P_SIGNALS_API_TOKEN` are empty. Only `P_SEARCH_API_KEY` (Parallel) and `P_SIGNALS_API_KEY` (PredictLeads) are set. Never print, log or commit key values. Without the model key, runs stop at the first model step (audit F4) and chat returns 503. Build and test against the fakes; mark the real-run cost report as blocked until the owner fills the keys.
3. **Uncommitted work.** Branch `feat/research-files` (HEAD `aaf7330`) has 214 uncommitted entries (138 modified, 76 untracked: migrations 0049 to 0052, `src/documents/`, `src/research_files/`, `runs/stop.py`, `runs/sweeper.py`, `connectors/cache.py`, `llm/reply_cache.py`, 4 new routes, 35 web files). Before your first change, commit that state as one snapshot commit (`wip: snapshot of feat/research-files before final build`) without editing it, then create `feat/final-product` from it and do all your work there. Check that no secret is in the snapshot first: `web/.csp.mjs` and `web/.probe.mjs` contain test login values; move those values to an untracked file or environment variable before committing.

### A2. How a run works today

- Start: `POST /v1/runs` in `src/account_intelligence/api/routes/runs.py:111` (`create_run`) picks the input mode (`_input_mode`, :186) and calls `runs/lifecycle.py:125` (`create_run`). That reserves wallet credits (:203), writes **two** run rows (`account_runs` and the legacy `runs` table, :189) and seeds tracker rows, then enqueues slice 0 (:182). No credits returns 402 (:178). File mode goes through `api/routes/file_runs.py:116`.
- Worker: `POST /tasks/run-slice` (`api/routes/tasks.py`) chooses one of **three graphs**: `FileGraphFactory` (`src/documents/graph.py`), `AbmGraphFactory` (`src/agents/graph.py:213`, wiring :667-710, used when the run mode is pull/build or the `abm_agents` flag is on, default off, `src/agents/production/wiring.py:147`), otherwise Workflow v1 (13 stages in `config/workflow_v1.yaml:20-32`, `workflow/engine.py`, `workflow/stages/`). All run through `workflow/runner.py execute_slice`. `worker/__main__.py` + `worker/job.py` is a Cloud Run Jobs variant.
- State: `account_runs` (0002, statuses 0010), legacy `runs` (0004), LangGraph Postgres checkpoints (`platform/checkpoint.py`, thread id `<tenant>:<run>`), `run_leases` (0007), `tool_calls`, `model_reply_log` (0049).
- Run states: `runs/transitions.py` has QUEUED, RUNNING, AWAITING_APPROVAL, DISTILLING, VERIFYING, COMPLETED, PARTIAL, BUDGET_EXCEEDED, FAILED, CANCELED. A second, stale enum in `db/enums.py:50` serves the legacy table.
- Where runs stop today: cap approval interrupt (24h timeout); no model key (PARTIAL `model_not_configured`, `agents/graph.py:190`); Word renderer `node` NotConfigured in staging and production (PARTIAL `builders_not_configured`, `workflow/stages/core.py:212`); no Summary narrative (`build_failed: missing_narrative`, `exports/readers/summary.py:243`), so **company mode cannot complete even on the hermetic stack** (open item OI-IM-12); slice ceiling (FAILED, `runner.py:150`); gate failure (PARTIAL `gate_failed`).
- History (cost audit): 27 to 28 runs, **0 COMPLETED**, 1,902 paid calls of which 612 distinct, $221.64 provider spend, model spend never recorded.

### A3. Pieces you will reuse

- Ledger: `src/account_intelligence/connectors/ledger.py`, entry `connectors/service.py call_operation`, cache `connectors/cache.py`. Tables `tool_calls` (`UNIQUE (run_id, args_hash)`), `tool_call_events`, `run_leases`, `spend_defects`, `model_reply_log`. Idempotency key `args_hash(provider_code, operation, args)` (`ledger.py:66`). **It currently blocks runs** via the cap decision (`ledger.py` ~478-540 calling `spend.gate.decide`) and `connectors/budget.py reserve` ($5 and 60 calls per run, `config/providers.yaml:9-10`). Remove the blocking, keep the recording.
- Grading: product grade is one pure function in `grading/product.py` (grades `confirmed`, `probable`, `hypothesis`, `lead`, `internal`; DB CHECK in `0001_core.sql:43`). Fix: `db/enums.py:16 Grade` lacks `internal`; display label for internal is "Internal Only" (`product.py:80`), change to "Internal".
- Gate: `src/documents/gate.py` `PreDeliveryGate` (checks tracker_rows, fact_citations, client_dashes, client_terms, leak_check, internal_label; results in `run_gate_results`, `leak_checks`). Wired only in the uncommitted `app/container.py:430-432` and `:576`. The old `src/gate/` package has no importers: delete it once its useful checks are ported or confirmed covered.
- Builders: `src/exports/builder.py:206 build_all`; Word in `exports/word/` (`EXPORT_RENDERER`: `python-interim` works, `node` NotConfigured); workbooks `exports/excel/people.py`, `jobs.py`, `tech.py`. Per D5 the skill's own scripts replace these where the skill provides them.
- Route catalog: `api/policy.py` (`ROUTE_POLICIES` :78, `register()` :156) with roles, principal types, audit class (`read`, `run`, `export`, `admin`, `none`), tenant source. `api/guard.py route_guard` (:64) refuses uncatalogued routes. Check script `scripts/check_route_catalog.py`. Tenant roles today: admin, analyst, seller, viewer (`policy.py:26`, `config/roles.yaml`); collapse to one client role per section 3.
- RLS: `app_tenant()` in `migrations/0002_rls.sql:20-22`; role `app_rw` NOBYPASSRLS; per table `tenant_id DEFAULT app_tenant()`, ENABLE + FORCE, policy `tenant_isolation`. Session: `db/session.py:66 tenant_txn`. Tests: `tests/isolation/` with matrix `tests/isolation/matrix/matrix.yaml`, plus `scripts/check_rls_catalog.sql`, `scripts/run_isolation.sh`. Migration 0048 has two SECURITY DEFINER functions without the required `-- reviewed:` comment; 0050 `runs_orphaned()` is cross-tenant by design, review it.
- Operator plane: `api/operator.py` (staff JWT, `STAFF_JWT_*`), routes under `/operator/v1` (`operator_admin.py`, `operator_view.py`, `ops.py`). Web console at `web/app/operator/` with Google sign-in. Flags `checkpoint_unit`, `model_phase1`, `model_phase2` exist in the console but **nothing in `src` reads them**.
- Connectors: `config/providers.yaml:45-155`, code `connectors/providers/`: `p_search` Parallel, `p_scrape` Firecrawl, `p_signals` PredictLeads, `p_people` BitScale, `p_org` Sumble. Secrets via `connectors/security.py:184 load_secret`. Dev wires them only with `CONNECTORS=on`.
- Files: `src/documents/` (service, scan, sandbox, redact with strict/standard/minimal, chunking, embed, extract, gate, graph, leakcheck, company), migration 0052, `routes/documents.py` (11 routes). Embedder is `HashingEmbedder` (lexical, not semantic); `account_intelligence/rag/` exists but is not wired; malware scanner is a local signature scanner; the in-process parser crashed natively (exit 134, OI-IM-13), so parsing must move into the sandbox subprocess.
- SSE: `GET /v1/runs/{id}/events` (`runs.py:727`) and chat `turns/stream`.

### A4. Duplicates and dead ends to consolidate

- Two private-file systems: `research_files` (migration 0051, `routes/research_files.py` at `/v1/files`, `src/research_files/`) and `documents` (0052). Keep `documents`, expose it at the `/v1/files/*` paths in section 6, delete `research_files` (routes, module, migration via a new down-and-drop migration, tests, web lib).
- Three graphs plus two run tables. Target: one run state machine (section 3) on `account_runs`, one skill executor for company and deep runs, the file pipeline as a mode of the same executor. Retire Workflow v1, the ABM graph and the legacy `runs` table **after** the executor passes acceptance tests 1 to 3, in their own commits.
- Refresh engine `src/refresh/` is pure logic, wired to nothing; the web keeps schedules in `localStorage` (`web/lib/schedule.ts`). Wire the engine to the worker and the `PUT /v1/accounts/{id}/schedule` route; delete the localStorage path.
- Checkpoint UI logic (Continue 5 times, Auto continue) is client-side only (`web/components/ws/RunSteps.tsx:144`) on top of `/v1/runs/{id}/cap`, `cap/approve`, `spend/live`. Move the decision server-side (section 5) and delete the cap endpoints.

### A5. Everything to delete (D1, D2)

Remove routes, tables (new forward migration that drops them; keep history), modules, config, tests, nav items and contract entries (`docs/api/openapi.json`, `web/lib/gen/api.gen.ts`). No flags, no stubs.

- **Wallet and credits:** tables `wallets`, `wallet_ledger` (0001, RLS 0002); `runs/lifecycle.py` (InsufficientCredits :65, debit :203, `settle_once` :430); `config/billing.yaml`; ABM `finish` node wallet settle; `credit_cap` in `RunBody`; the 402 path in `runs.py:178`; tests in `tests/spend/`, `tests/api/test_spend_v2_routes.py`, wallet fixtures in `tests/conftest.py`, `tests/system/test_whole_run.py` wallet parts.
- **Plan limits and monthly pool:** `src/metering/` (limits, events, reconcile, usage_summary; keep only what the read-only usage report needs), migration 0025 tables (`usage_events` may stay for reporting; drop `plan_limits`, `plan_assignments`, `usage_counters`, `usage_thresholds`), `admin/quotas.py` and 0026 `quota_versions`, `config/plans.yaml`, `plan_limits` in `me.py`, the account limit at `routes/accounts.py:146` and `routes/runs.py:146`, routes `usage.py` (replace with read-only reporting) and `/quotas`, `/plan` in `operator_admin.py`, tests `tests/phase3/test_ws7_metering.py`, `test_ws8_admin.py`.
- **Per-run and per-pull caps:** `src/spend/` (about 25 modules), `agents/production/credit.py`, ABM nodes `cap_approval`, `spend_gate`, `sense_approval` (the sense check itself stays, its approval interrupt goes; checkpoints replace it), `workflow/stages/spend.py`, routes `spend.py`, `spend_v2.py` (and its operator router), the ledger cap check, `connectors/budget.py` reservation limits, migrations 0015, 0017, 0038 tables that only serve caps, `config/spend.yaml`, `pricing.yaml` (fold provider prices into the new rate table), `tests/spend/`.
- **Throttling tied to plan:** `src/governance/` (throttle, monitoring_budget, account_cost), migration 0031 tables, `config/budgets.yaml`, `/v1/monitoring/budget` in `signals.py`, `tests/phase4/test_ws6_governance.py`. Keep `identity/rate_limit.py` and `api/ratelimit.py` (login abuse protection) and `connectors/ratelimit.py` (provider pacing); they are not caps on the user.
- **Chat limits:** the thread turn/spend cap that returns 429 `chat_limit_reached` in `api/routes/chat.py`.
- **Gmail and Drive connectors:** `src/connectors_client/` (gmail, drive, oauth, framework, quarantine, access_log, scopes, notes; leave `crm_*` only if CRM is in scope, otherwise delete with its `/v1/crm` routes too and record the decision), `api/routes/connectors.py` (8 routes) and its uses in `api/tenant_plane.py`, `tenancy/catalog.py`, migration 0024 tables, `config/connectors.yaml`, the `connectors` plan dimension, `tests/phase3/test_ws6_connectors.py`, `tests/fakes/connectors.py`, matrix entries, `web/app/(client)/settings/connectors`.
- **6th Library, picker, seed upload, publishing:** no backend exists. Confirm with grep, remove mentions from `docs/FRONTEND_BACKEND_GAPS.md` (G-10, G-15), `SCREENS.md`, `docs/FRONTEND_AUDIT.md`, `docs/UI_BACKEND_AUDIT.md`. Do not build them.
- **Web leftovers:** every file in `web/LEGACY_TO_DELETE.txt`, all redirect-stub routes (`alerts`, `audit`, `chat`, `downloads`, `feedback`, `usage`, `runs` list, `research-files/*`, `settings/*` and sub-pages, `accounts/[domain]/history`, `accounts/[domain]/tables/[table]`, operator `configuration`, `cost`, `failures`, `spend`, `support`, `tenants`), `components/ws/SettingsNav.tsx`, `SettingsFrame.tsx`, `RefreshDialog.tsx`, `WebOffer.tsx`, `ui/DataTable.tsx`, `ui/Scene.tsx`, `ui/fonts/`, unused dependencies (`lucide-react`, `@radix-ui/react-popover`, `@radix-ui/react-tooltip`), the probe scripts `web/.csp.mjs`, `.probe.mjs`, `.gal.mjs`, `.shots.mjs`, the `exclude` list added to `web/vitest.config.ts` (fix or delete the excluded tests instead), and the stale design docs `docs/DESIGN.md`, `docs/DESIGN_SYSTEM.md` (replace with one `docs/DESIGN.md` that states section 8).
- **Repo root clutter:** `venv2/`, `test-results/`, `screenshots/`, `screenshot_all.*`, `screenshot_test.spec.ts`, `verification-report.txt`, `run_sql.py` (check each is unused first; add to `.gitignore` where they are generated).

### A6. Known defects to fix (found by running the app and reading the code)

Backend:
- **Chat returns 503 on the hermetic stack.** `app.state.chat_service.provider` is None whenever `container.llm` has no `.client` (`api/main.py:71-77`): `FakeModel` has no client, and dev without a key gets `UnconfiguredLlm` (`container.py:361-373`). Make the fake model serve chat in dev and tests; with no key and no fake, return a clear problem response the UI can show.
- Company mode ends `missing_narrative`; the Word build must succeed (audit F2, OI-EXP-1). The `node` renderer must not be the production default while it is NotConfigured.
- `config/model_routing.yaml` still names `claude-haiku-4-5-20251001` with no prices and the router is off (F11). Models must come from settings only (section 4.2).
- Failures like run bc0b94b8 (`unconfirmed_stated`) are only half fixed: the run now ends PARTIAL, but the wording rule that causes it is unchanged.
- A missing or foreign account renders an empty state with HTTP 200; return 404 without revealing whether it exists in another tenant.

Frontend (`web/`):
- **First question of a new chat vanishes on error.** `components/ws/Ask.tsx:164-193`: on failure the turn is removed, then `finally { router.refresh() }` re-renders `app/(client)/page.tsx`, whose `<Ask key={opened?.id ?? "new"}>` changes key from `"new"` to the thread id, remounting Ask and losing the error and the draft. Do not refresh on failure, keep the key stable, and persist failed turns with their error. `readFrames` silently drops malformed frames, and a stream that ends without `done` shows an empty answer: show an error instead.
- **CSP blocks styles.** `web/proxy.ts` sets `style-src 'self' 'nonce-X'` but the app never passes the nonce. The Radix Dialog scroll lock (`AccountHeader.tsx`) injects an un-nonced `<style>`, and Next's default 404/error pages use inline style props. Pass the nonce (read `x-nonce`, `setNonce` for `get-nonce`) or replace the dialog; add `app/not-found.tsx`, `app/error.tsx`, `app/global-error.tsx` built from the design tokens. Zero CSP console errors on every page is an acceptance check.
- **Mobile menu:** `components/ws/WorkspaceShell.tsx:68-77` and `ui/shell.css:244-254`. The sidebar becomes a static block that pushes the page down; Escape, outside click and focus return are missing. Make it an overlay with Escape, focus trap, focus return and `inert` on the page. The operator console sidebar is hard-coded `data-open="true"`; give it the same behaviour.
- Every page shares one `<title>`; add per-page metadata.
- Tables: `components/ws/TableScreen.tsx` + `DataGrid.tsx`. Row numbers restart on every page (offset never passed). "Download as Excel" posts `kind:"csv"` (`TableScreen.tsx:44-57`) and produces CSV; make it a real `.xlsx` from the skill's workbook builder. Raw code `export_rules_refused` is shown (`TableScreen.tsx:110`, from `src/views/materialize.py:141`); map every reason code to plain words. Hide or disable search, filter and download when there are no rows.
- Files tab (`components/ws/FilesTab.tsx`): downloads fire hidden anchors 400ms apart; use the zip endpoint. "Build files" treats any non-`delivered` status as failure and never polls; follow run events. "Download selected" and "Download all" use different disabled styles.
- New research (`app/(client)/research/new/page.tsx`): the "Also check public sources" checkbox renders only after files are added (l.159-164) while the text promising it shows at once; "Estimate." prints no number (l.59); "Website (optional)" contradicts its hint and the `domain_required` error; tabs lack `tabpanel`, `aria-controls` and arrow keys; hint and error are not linked with `aria-describedby`; the hidden file input is focusable and announced twice; the estimate shows dollars ("Model reading, about $X"): show units as a range instead.
- Account page: the list says "Last run failed" while the detail page says "Last researched 12 October" with no failure and "A summary appears after the first research run"; "What changed since 11 October" shows on a first scan. Show one consistent run status with the failure reason and a retry, and show "What changed" only after a later refresh.
- Composer textarea removes its focus outline (`components.css:146`) with no replacement; the page autofocuses the composer so the skip link is skipped.
- `.contents` class is used in `<dl className="dl">` (`research/new/page.tsx:167-169`, `RunSteps.tsx`, operator runs page) but defined nowhere; label and value pairs land in the wrong cells.
- Next.js prefetching produced over 400 requests in a short session, many aborted repeats; set `prefetch={false}` on sidebar and table links or limit it.

Design-rule violations in `web/ui/*.css` (fix all, see section 8): second light grey `--hover #f5f5f7` and `--accent-press`; overlay `rgba(0,0,0,.3)`; accent used for `.grade.probable`, running step, drag-over, checkbox `accent-color`, `.cite` border; status colours used for "N new" signals (`AccountRows.tsx:66`), "Saved." (`AccountHeader.tsx:31`), `.btn.danger`, file stages; `.cite` 11px with 4px radius; `h1` letter spacing; controls under 40px (`.btn.sm` 32, `.seg` 38, `.composer .select` 38, `.select.inline` 36, `.nav .recent a` 36, `.table th button` 24, `.link` 0, `.auth-links button`); off-scale spacing (2, 4, 6, 10, 12, 20px); input borders `--line #e4e4e8` at about 1.3:1 fail the 3:1 non-text contrast rule, so use a darker border for inputs.

### A7. Documentation that is wrong

- `README.md`: says "Claude 3.5 Sonnet" (config uses `claude-opus-5-5`, `claude-fable-5-1`, `claude-sonnet-5-5`; chat default `claude-sonnet-5-5`); demo login `doctor@synthetic.local` does not exist; "1-click Demo Workspace" button does not exist; "20 unit and contract tests" is wrong; fake thread names and generators are presented as features.
- `docs/STATUS.md`: marks every phase Completed although no run has ever completed; model allowlist omits Sonnet 5.5; says `.env.example` was retired but it exists.
- `TESTING.md`: "Vitest 20/20 (7 files)" and "8/8 services healthy" are wrong.
- `BUILD_LOG.md`: "vitest 101 passed" does not reproduce.
- `docs/SCREEN_MATRIX.md`: claims 50/50 parity on screens that are now redirect stubs.
- `BACKEND_AUDIT.md`, `DECISIONS.md`: say migrations end at 0047; the tree is at 0052.
- `docs/sources/` (PRD, SaaS v2 docx, Architecture v2 PDF) is gitignored, so those sources exist only locally. Keep it that way; reference them by path.

### A8. Test baseline (2026-10-13)

- `pytest --collect-only`: 3,794 tests. Without Postgres (`-m "not requires_postgres and not eval and not large_file"`): 2,670 passed, 12 skipped, 0 failed in 331s. The 1,112 database tests were not run. `make test` runs isolation first and recreates the `ai_test` database.
- The audit's "seven failing tests" (F6): six now pass; `tests/system/test_fake_model_scenarios.py` (needs Postgres) is unverified.
- Web: `npm --prefix web run test -- --run` reports 8 files, 29 tests passing, but only because the uncommitted `web/vitest.config.ts` excludes 7 of 15 files. With all 15: 6 files and 16 tests fail (`sourceLabel is not a function`, undefined `StoredSummary` and `GateDefects`, grade class expectations, `CitationChip` reading `.source` of undefined, `missing :root[data-theme="dark"]`, two copy-rule failures). Fix or delete each; no exclude list.
- Playwright config (`web/playwright.config.ts`) has no `testIgnore`, so legacy `e2e/parity/` and `e2e/review/` specs run against stub routes. Rewrite e2e around the screens in section 9; `@axe-core/playwright` is installed, use it.
- `pytest-timeout` is not installed; add it.

---

## 0. Understand first (deep mode, before any code)

Work in deep, high-effort mode. Do not start building until you can explain the system. Use sub-agents in parallel for the reading below, and verify by running things, not only by reading.

1. Read, in this order: (a) section A of this prompt, then `docs/audit/2026-10-13-full-project-audit.md`, `docs/audit/2026-10-13-research-cost-audit.md`, root `AUDIT.md` (durable runs A1 to A14) and `PLAN.md`; (b) the ABM skill package in `skills/abm-account-research/` (`SKILL.md`, then `references/checklist.md`, `phase1-pull.md`, `phase2-build.md`, `tools.md`, `signals.md`, `output-spec.md`, `lessons.md`; if absent, follow A1.1); also `ABM_Skill_Readers_Guide_v2.docx` and `docs/abm/*` (`AGENTS.md`, `CONVERSIONS.md`, `RECONCILIATION.md`); (c) the PRD and SaaS architecture v2 in `docs/sources/`; (d) the repository paths named in A2 to A4 and their tests.
2. Establish a baseline: commit the snapshot (A1.3); start the stack (`compose.yml`, `CONNECTORS=off`, fake model); run the full Python suite including Postgres tests and isolation, and all 15 web test files without the exclude list; record pass and fail counts with test names. Run the skill's own scripts on a sample input. Trace one company run and one file run through the code from request to deliverable and record exactly where each stops.
3. Write `docs/build/UNDERSTANDING.md` (two pages at most): how a run works today, file by file; what the audit found broken; how the skill works (eight steps, which reference file is opened at which step, which script runs when, which parts assume a single operator); the full list of what you will delete; the full list of what you will add; risks and open questions.
4. Then continue without waiting. Stop to ask only for a decision that cannot be undone and could reasonably go either way (dropping a table with data in it counts; deleting code under git does not).

The design prototype is `/home/sakib/Downloads/Account Intelligence by 6th (2).pdf` (18 pages; the two older PDFs differ only on pages 1 and 2). It is a reference for flow and content only. Section 9 maps each of its pages to what to build, keep or drop. Where it is more decorative than section 8 allows, section 8 wins.

## 1. What the product is

A multi-tenant service for B2B sales teams. A user starts research in one of two ways, then asks questions of the result in a chat, in one of two search modes. It returns a graded brief, a deduplicated senior people list, a 33-row coverage tracker (`config/checklist.yaml`, reconciled with the skill's own `references/checklist.md`; record any differences in `UNDERSTANDING.md`), and downloadable files. It never sends outreach.

## 2. Decisions already made (do not re-ask, build them)

D1. Only two ways to start research.

- Company name: name plus optional website. Runs the full ABM skill on public sources. The website is required only when the name does not match an existing account or cannot be resolved; the label and error say exactly that.
- File: one or more files chosen from storage (the workspace Files area, or uploaded from the user's device). Research is done from that data only. Public sources are used only if the user turns on "Also check public sources".

Delete everything else from the add-account flow: the "6th Library" / ready-made research picker, the library seed upload screen (admin), library publishing per client, and the Gmail/Drive connector screens. Remove routes, tables, tests and nav items (list in A5). Do not leave dead code behind a flag.

D2. No caps set by admin. Remove: account cap per user, monthly credit pool, per-pull credit caps, throttling, rate limits tied to plan, chat turn limits (list in A5). An admin cannot limit how much a user researches or asks. Admin can still see usage, and can enable or disable a user or a whole client (access control, not a cap). Idle-access expiry may remain as an optional per-client setting, default OFF, labelled as access control. The only control over spend is the user's own approval at checkpoints (D4). Keep the ledger for accurate cost reporting; it never blocks a run.

D3. Two search modes, chosen by the user in the chat composer (a visible segmented control: Normal | Deep; the existing composer already has it, keep it).

- Normal search: answers only from what the workspace already holds (stored facts, tracker, extracted file content, previous deliverables). No web, no data-broker calls, no skill run. Fast. Every answer cites its source rows as clickable numbered links inline.
- Deep search: runs the ABM skill workflow for the account, exactly as the skill describes (section 4), going to the web and data tools, refreshing the stored facts and files, then answering. Deep search is also what powers first research for a company name, what "Refresh from web" on an account runs, and what a scheduled refresh runs. Today Deep is a client-side trick (`Ask.tsx:117-125` posts a run, then re-asks in Normal); make it a server-side mode of `POST /v1/chat`.
- **How the switch looks (owner decision, replaces the segmented control):** follow the composer on page 1 of the prototype PDF. One composer box: the question field on top; below it a row of small chips, `+ Upload a file`, the scope chip (`All 6 accounts` or the account name), and a toggle chip **`Research: off`** / **`Research: on`**; the `Ask` button on the right. `Research: off` is Normal (stay local, answers from the workspace). `Research: on` is Deep (searches the web, updates the account, then answers). The toggle is a real button with `aria-pressed`, shows its state in words, resets to off for each new message, and the "on" state uses the accent colour with the text label. When an off answer cannot be answered from stored data, show one button, **"Research this"**, which resends the same question with Research on. Helper line under the composer: "Research off answers from your workspace. Turn Research on to search the web and update the account." Internal names in code, API (`mode: "normal"|"deep"`) and this prompt stay as they are; only user-facing text changes.
- **Model picker (owner decision):** below the composer, at the left of the helper line, a small model chip like claude.ai's (for example `Claude Opus 5.5 ⌄`) opens a menu with three choices: **Claude Opus 5.5** (`claude-opus-5-5`, default), **Claude Fable 5.1** (`claude-fable-5-1`, labelled "Most capable, slower, higher cost") and **Kimi K3**. Rules:
  - The choice applies to chat answers in that thread and is remembered per thread; a new chat starts on the default. It is a real menu button with a visible label, keyboard support and `aria-haspopup`.
  - Research runs (Research: on, New research, Refresh, schedules) keep the Phase 1 / Phase 2 routing from admin settings so the skill and the gate behave the same whatever the chat model is. Show "Research uses the workspace research models" in the menu so this is not a surprise.
  - The list comes from the server (`GET /v1/models`, already exists; reuse `config/chat_models.yaml` and the allowlist in `config/models.yaml`). A model whose key is not configured is shown disabled with "Not set up", never hidden silently and never failing at send time.
  - **Kimi K3 is a different provider (Moonshot AI).** Add it behind the existing model-provider interface as a second provider, with its own key read from the environment (`MOONSHOT_API_KEY`, empty by default; never logged, never sent to the browser). Look up the exact model id, endpoint and streaming format in Moonshot's current documentation; do not guess them. Claude-only features (Claude's server-side web tools, citation blocks, adaptive thinking) must not be assumed for Kimi: with Kimi selected, answers come from the workspace only and cite stored facts the same way. Record in the ledger which provider and model produced each answer, with its token counts and price from the rate table.
  - Sending a tenant's workspace data to a second AI provider is a data-handling change: add a per-client setting in the admin console, "Allow non-Claude models", default **off**, and show Kimi only where it is on. List this in "Decisions to flag".
- Scope the switch per message. Default is Normal. If a Normal answer cannot be answered from stored data, say so plainly and offer "Run deep search" as a button. Never go to the web in Normal mode.

D4. Checkpoint approval, like Claude's "continue?" prompt. Long work runs in chunks. When a run has used one chunk of metered units, it pauses and asks the user: "This has used N units. Continue with another N?" Details in section 5.

D5. The skill is executed, not re-implemented. The backend runs the uploaded ABM skill. Do not hand-translate its checklist into code. Section 4. The existing `config/skill/*.yaml` and `docs/abm/*` encode the skill by hand from the reader's guide (`archive_text: null` throughout); they are replaced by the registry and the executor, keeping only what section 4.7 needs as runtime configuration.

## 3. Architecture changes

- Skill registry: table `skill_versions(id, name, version, storage_uri, sha256, active)` with forced RLS where tenant-scoped (the registry itself is platform-wide; make it readable by `app_rw`, writable only by the operator plane). Upload and activate a new skill package from the admin console (Settings) or by commit to the repo (`skills/abm-account-research/` loaded on deploy). Every run records the skill version id and sha256 it used. Replace `config/skill_versions.yaml` and the `skill_config_version` pin in `agents/adapters.py:222`.
- Skill executor service (worker): an agent loop that runs the skill (section 4). It replaces Workflow v1 and the ABM graph for company and deep runs; reuse `platform/checkpoint.py` for durable state, `runs/stop.py`, `runs/sweeper.py` and the lease logic.
- Run state machine on `account_runs` only: `QUEUED → RUNNING → PAUSED_AWAITING_APPROVAL → RUNNING → … → GATE → COMPLETED | FAILED | STOPPED_BY_USER`. Map and migrate the old states (PARTIAL, BUDGET_EXCEEDED, CANCELED, AWAITING_APPROVAL, DISTILLING, VERIFYING) and drop the legacy `runs` table and its enum. Every state change is persisted so a run resumes after a crash or deploy. A stop keeps all data.
- Meter service: counts metered units per run and per session in real time (section 5). Record model tokens for every call (the legacy path never did; `model_reply_log` is the start).
- Events: server-sent events per run (extend `GET /v1/runs/{id}/events`): step started, step finished, units used, pause for approval, file ready, completed, failed.
- File storage: quarantine bucket → scan → parse (in the sandbox subprocess, not in process) → promote. Per-tenant prefix and per-tenant key. One file system (`documents`), see A4.
- Keep: forced RLS on every tenant table, tenant from verified session only, audit log, route catalog with roles and audit class. Client roles: one role only (anyone in a client workspace can do everything inside it and nothing outside it); collapse admin, analyst, seller, viewer and update `config/roles.yaml`, the catalog and the isolation matrix. Clients must never be able to discover that other clients exist (uniform 404s, no cross-tenant counts, no tenant ids in errors).

## 4. Skill executor: run it the way Claude runs the skill

1. Progressive loading. Put `SKILL.md` in the agent's instructions. Give the agent a `read_skill_file(path)` tool, restricted to the active skill version's files, so it opens each `references/*` file only when a step calls for it, as the skill says. Do not paste all references into every prompt.
2. Follow the skill's eight steps (set up, summary homework, detailed homework, people, distill, build files, pre-delivery gate, delivery), including split mode: Phase 1 pulls everything into files and never interprets; Phase 2 starts with the mandatory sense check (entity boundaries, adjacent organizations, hidden titles, contradictory facts), then builds. Model routing is configuration: a cheaper model for Phase 1, a stronger model for the sense check and Phase 2. Do not hardcode model names; read them from settings (`model_phase1`, `model_phase2`, editable in the admin console, validated against the allowlist in `config/models.yaml`). Delete the stale Haiku entry in `config/model_routing.yaml`.
3. Real tools, not simulations. Expose the data tools described in `tools.md` (Parallel, Firecrawl, PredictLeads, BitScale, Sumble; providers `p_search`, `p_scrape`, `p_signals`, `p_people`, `p_org`) as agent tools. Every call goes through the paid-call ledger, with idempotency keys, and stores the raw response before any work is done on it ("pull once, work from rows"). Reuse `connectors/cache.py` so a resumed or retried run never pays twice (the cost audit found two in three calls were repeats).
4. Run the skill's own scripts from `scripts/` and `assets/` in a sandbox with no network access: the Word document helper, the people and jobs workbook builders, the tool-name stripper, the table-row-drop check, the link and banned-dash check, the third-party domain scan. If a script is missing or fails, the run fails visibly. Do not rewrite their logic in the app. Where `src/exports/` duplicates a skill script, the skill script wins and the duplicate is deleted.
5. Grading is computed in code (`grading/product.py`), never taken from the model's word. Grades: Confirmed, Probable, Hypothesis, Lead, and Internal for file-derived facts (section 7). Every fact stores grade, URL or file citation, and the exact sentence relied on. Not finding something is never more than a Hypothesis.
6. The gate must run and must be able to pass. Use `src/documents/gate.py` plus the skill's pre-delivery checks (coverage rows all have a status and reason, every fact graded and linked, no banned dashes, no data-tool names or credit counts in client-facing files, third-party domain scan). A failed gate loops back to the failing step with the defect attached, up to a limit (setting, default 3), then marks the run FAILED with the reason shown to the user in plain words. Fix the Word summary build (`missing_narrative`, A2) so it succeeds, and fix the failing tests from A8 or document each exclusion.
7. Adapt only the parts the skill says are single-operator. Do this as runtime configuration, not by editing the skill text (the reader's guide lists them; check against the real `SKILL.md`):
   - "Pursuing client" and setup answers come from the tenant profile.
   - The skill's "agree a credit cap first" step is answered by the checkpoint mechanism (section 5). Intercept that question and respond from run state. The run proceeds to its first checkpoint without asking.
   - "Ask before spending unapproved credits" and "sense-check pulls once approved" are also answered by checkpoints, not separate approvals.
   - The BitScale people-pull consent step becomes a one-time tenant-level acknowledgement stored with timestamp and user, shown before the first people pull.
   - The "Rajesh Only" internal file is never generated.
   - Pass 1 internal history (mail, call notes, Drive) is disabled. In file mode, the uploaded files are the internal history.
   - Skill updates come from the skill registry, not in-chat repackaging.
8. Lessons log and checklist are read from the active skill version. Corrections are made by publishing a new skill version. `config/lessons.yaml` and the hand-built `docs/abm/LESSONS.md` go.
9. Client-facing output never names data tools, credit counts or internal call references. Run the skill's stripper and check on every deliverable before release.

## 5. Checkpoint approval (the "every 1,000, should I continue?" logic)

- Metered unit = model input and output tokens plus data-tool spend converted at a configurable rate table. Checkpoint size is a platform setting, `CHECKPOINT_UNIT`, default 1,000 units, as requested. (Note for the owner: a deep run is large, so at 1,000 it will pause often. That is why Auto-continue exists. Making the size larger is a one-line settings change.) The `checkpoint_unit` operator flag already exists in the console; make the backend read it.
- A run pauses between steps or tool calls, never mid-call. It may overshoot slightly; record the true number.
- When the run reaches a boundary it moves to `PAUSED_AWAITING_APPROVAL`, saves state, and the user sees a card in the run view and in the chat:
  - What was done since the last checkpoint, in plain language.
  - Units used so far and units used in this chunk.
  - What the next chunk will do and a rough estimate of how many chunks remain (shown as a range, with an "estimate" label).
  - Buttons: Continue (one more chunk), Continue 5 times, Auto-continue this run, Stop and keep what you have.
- "Auto-continue" is chosen by the user for that run only, stored server-side on the run. It is not an admin setting and not a cap. Stop keeps everything found so far and still builds the deliverables from available data, marked partial, with the tracker showing which rows were not reached and why.
- A paused run waits indefinitely (remove the 24h approval timeout). Notify the user in-app, and by email only if an email channel is configured (it is NotConfigured today; do not send anything).
- Scheduled refreshes run unattended. When a user enables a schedule they explicitly choose "Auto-continue scheduled runs" on or off. If off, a scheduled run pauses at the first checkpoint and notifies the user.
- Normal search is usually a single completion and does not interrupt. If a Normal question spawns multi-step work that crosses a checkpoint, the same card appears.
- Admins see every pause, approval and total, as read-only reporting. They cannot approve, block or limit.
- Persist a `run_checkpoints` table (forced RLS, in the isolation matrix): run id, sequence, units at pause, units in chunk, decision, decided by, decided at.

## 6. Backend API (add to the route catalog with roles, audit class and tenant checks)

- `POST /v1/runs` body `{input_mode: "company"|"file", company_name?, website?, file_ids?, enrich_public?: false, lanes?, people_scope?}` → run id. Exactly one input mode. No `credit_cap`, no `depth`, no 402.
- `GET /v1/runs/{id}`, `GET /v1/runs/{id}/events` (stream), `POST /v1/runs/{id}/checkpoint` `{decision: "continue"|"continue_n"|"auto"|"stop", n?}`. Delete `/cap`, `/cap/approve`, `/spend/live`.
- `POST /v1/chat` body `{account_id|null, message, mode: "normal"|"deep", thread_id?}` → streamed answer with citations `[{n, fact_id|file_id, title, locator, url}]`; deep mode creates or resumes a run and streams its step and checkpoint events into the same thread. Keep thread list/get/delete. Decide whether `/v1/chat/provider-key` (tenant's own model key) stays; it is not in this spec, so remove it unless you find a reason in the PRD, and record the decision.
- `POST /v1/files/uploads` (presigned upload), `POST /v1/files/{id}/complete`, `GET /v1/files` (workspace storage list), `GET /v1/files/{id}/status`, `DELETE /v1/files/{id}`. Backed by `documents`, replacing both `research_files` and the 11 `documents` routes where they overlap.
- `GET /v1/accounts`, `GET /v1/accounts/{id}` (narrative, findings or changes, deep-dive counts, last run status with failure reason), `GET /v1/accounts/{id}/people`, `/jobs`, `/technology`, `/news`, `/sources` (sortable and filterable server-side, keyset paging with absolute row numbers), `GET /v1/accounts/{id}/deliverables` (list with type and size), `GET /v1/accounts/{id}/deliverables/download?ids=a,b,c` (one id = file, several or all = zip).
- `PUT /v1/accounts/{id}/schedule` `{cadence: "3d"|"weekly"|"2w"|"monthly"|"off", auto_continue: boolean}`, wired to `src/refresh/` and the worker.
- Admin (6th staff only, separate auth plane at `/operator/v1`, existing staff JWT): clients (create, enable or disable), users (list with last active, enable or disable), usage (counts and categories), runs and spend (read-only), platform defaults (checkpoint size, model routing, rate table, gate loop limit), skill versions (upload, activate, roll back). The operator users list and full run list endpoints are missing today; add them.
- Regenerate `docs/api/openapi.json` and `web/lib/gen/api.gen.ts`; `scripts/check_route_catalog.py` must pass.

## 7. File mode (research from storage)

- Pipeline: quarantine → virus scan and size and type check (clamd in production; the local signature scanner only in dev) → parse in a sandbox with no network (fixes the exit-134 crash) → audio only if a transcription vendor is configured, otherwise reject with a clear message → redact per tenant policy (Strict, Standard default, Minimal; keep original and map) → chunk by type → embed into a namespace scoped to tenant and account (replace `HashingEmbedder` with a real embedder behind settings, or record why not; wire `account_intelligence/rag/`) → extract candidate facts → grade → fill the tracker → build deliverables → gate.
- File-derived facts get the grade Internal, linked to file and location (page, row, timestamp). Nothing becomes Confirmed from a file alone unless the user marks that source authoritative.
- Company resolution: take the company from the file (`src/documents/company.py`). If more than one is found, the user must choose before the run continues. Never guess.
- Tracker rows with no file evidence read "Not in uploaded data". Rows that need public sources read "Not searched (file only)". Absence is never stated as a finding.
- Two-output rule: anything derived from a file is private to the workspace. A client-facing, shareable deliverable may contain only public-source facts. Internal-view output is clearly labelled "From your files". Keep the leak check (`src/documents/leakcheck.py`: n-gram plus embedding similarity against private chunks) before any shareable file is released, and a planted canary-string test.
- Text inside uploaded files is data, never instructions. Wrap it in the untrusted-content boundary and test with a file that says "ignore previous instructions".
- Deleting a file removes its chunks, embeddings and object, and marks derived facts for re-evaluation (`fact_reevaluations`, `document_deletions` exist in 0052).

## 8. Design rules (apply to every screen, user and admin)

The product must look minimal, calm and professional. Easy to scan, easy to use, nothing that asks for attention. The token file `web/ui/tokens.css` is already close; A6 lists every violation to fix.

- Palette: white background, one dark text colour, one mid grey for secondary text, one light grey for borders and table rules (a darker grey only where a border must meet 3:1 non-text contrast, such as inputs), and one accent colour used only for the primary button, links and focus rings. Status colours (green, amber, red) only for grades and run state, always with a text label.
- Type: system font stack, no web fonts. Body 15px. Secondary 13px. One heading size (22px) and one page-title size (28px). Normal and semibold weights only. No letter spacing, no uppercase eyebrows.
- Shape: 6px corners, 1px borders, no shadows, no gradients, no illustrations, no emoji, no decorative icons. At most a few functional icons (upload, download, close), inline SVG, no icon library. Motion only for a progress indicator.
- Layout: left sidebar 220px, content column. Chat column max 720px. Tables up to 1100px. 8px spacing scale only. Prefer plain lists and tables to boxes; never put boxes inside boxes.
- Words: sentence case, short, plain labels. One primary action per screen, shown as the only filled button. Never show internal codes (`export_rules_refused`, `gate_failed`), provider names, credit counts or dollar amounts to client users.
- Controls: every control is a real button, link or input with a visible label and a visible keyboard focus. Targets at least 40px high. Text contrast at least 4.5:1. Works at phone width (375px) without horizontal page scroll. The mobile menu is an overlay that closes on Escape and returns focus.
- States: every screen has an empty state, a loading state and an error state in plain words with one next step. Global `not-found`, `error` and `global-error` pages follow the same rules.
- Security headers: zero CSP violations in the console on every screen.

## 9. Frontend: end-user app (few screens)

Left sidebar: workspace name, New chat, Ask, Accounts, New research, recent chats, then the user's name and Sign out. Nothing about caps, usage limits, schedules page or mail and drive.

Prototype mapping (`Account Intelligence by 6th (2).pdf`):

| PDF page | Prototype screen | Decision |
| --- | --- | --- |
| 1 | Ask home, "Web: off" chip, five suggestions | Build the composer with the "Research: off / on" chip (see D3); no heading and no starter questions |
| 2 | Accounts table with Lanes column | Build as specified below; Lanes column optional |
| 3 | Account detail with right-rail "Deep dive" counts | Build as tabs (below); counts may appear next to tab names; drop "From your mail and drive" |
| 4 | Answer thread with citations, "What this answer used" panel, "Refresh from web" callout | Build the thread and inline citations; drop the side panel; the callout becomes the "Run deep search" button in Normal mode |
| 5 | Refresh confirmation with credit costs and three scopes | Replace with the Run view and checkpoints; no credit numbers |
| 6 | Schedules page with credit pool | Drop the page; schedule lives on the account page |
| 7 | Add accounts with 6th Library grid and "12 of 34" | Drop; New research replaces it |
| 8 | People table with filter chips, priority and grade | Build as a data table (below) |
| 9 | Mail and drive | Drop |
| 10 | New research, file tab | Build, without "research credits" wording |
| 11 | File analysis result | Build as the Overview of a file-built account |
| 12 to 18 | Admin console | Build the five pages in section 10; drop credits, caps, library and "View as a client" |

Screens:

1. Ask (home, `/`). Empty state (owner decision): no heading and no starter questions, only the composer, centred vertically in the content column; after the first message it moves to the bottom and the conversation scrolls above it. Composer: as described in D3 (question field, `+ Upload a file`, scope chip, `Research: off/on` chip, Ask). Conversation scrolls above a composer pinned at the bottom. Answers stream. Citations are inline numbered links that open the source (URL, or file and location). No separate "what this answer used" panel. In Normal mode, if stored data cannot answer, say so and show one button, "Run deep search". In Deep mode, show a short live list of steps and, when paused, the checkpoint card. A failed question stays on screen with its error and a Retry (A6).
2. Accounts (`/accounts`). A table: account, last researched (with run state label), schedule, new signals. Click a row to open it. One button: New research. At phone width the account name never breaks mid-word and the table scrolls inside its own frame.
3. New research (`/research/new`). Two tabs (real tablist with panels and arrow keys): Company name and From a file. Company tab: name, website (labelled per D1), Start. File tab: drop area or "Choose from storage", file list with status (scanning, reading, ready, failed), a company confirmation when the file mentions more than one, a checkbox "Also check public sources" (off, always visible), Start. Show an estimate (a range) of units before starting.
4. Run (`/runs/[run_id]`). A vertical list of the skill's eight steps with done, running or waiting, units used so far, files as they appear, Stop. The checkpoint card appears here and in the chat: plain text of what was done, units used, then four buttons: Continue, Continue 5 times, Auto-continue, Stop and keep results.
5. Account (`/accounts/[domain]`). Title, one-line run status (with the failure reason and Retry when the last run failed), one paragraph summary, then tabs: Overview, People, Jobs, Technology, News, Sources, Files. Overview shows Key findings (graded) after a first scan, and What changed since <date> only after a later refresh, never on a first scan. A small settings line holds the refresh schedule (every 3 days, weekly, every 2 weeks, monthly, off) and an "Auto-continue scheduled runs" checkbox, saved to the server. "Refresh from web" starts a deep run. Account-scoped chat is the Ask screen with this account preselected. Files tab: a list of deliverables with type and size, checkboxes, Download selected and Download all (one file downloads as itself, several as a zip).
6. People, Jobs, Technology tabs are plain data tables: sortable on every column, filter dropdowns (for example priority A, B, C and fact grade), sticky header, absolute row numbers, copy, and a "Download as Excel" link that produces a real `.xlsx`. Sort and filter server-side. Data-grid component: `@tanstack/react-table` (headless, already in `package.json`, renders plain `<table>` so it follows section 8; record the choice and reason in `docs/build/PLAN.md`). Do not build an embedded Excel clone.
7. File analysis is the Overview tab of an account built from files: facts marked Internal with file and page, a "Not in your files" list, and a quiet line "From your files, private to your workspace". Buttons: Ask about these files, Also check public sources, Download internal summary, Delete files (with a real confirmation dialog, not `window.confirm`).

## 10. Frontend: 6th admin console (separate auth, same design rules, five pages)

Lives in `web/app/operator/` with the existing staff sign-in. Same tokens, white sidebar like the client app (the prototype's navy sidebar is dropped).

- Overview: counts of active clients and users, runs this week, questions by category only, isolation check status.
- Clients: a table. Click a client to see its members and an enable or disable control, plus optional idle-access expiry (default off). No caps of any kind anywhere.
- Users: a table with client, last active and an enable or disable control.
- Runs: read-only table of every run: client, account, type, units, pauses, approvals, tool spend by purpose, status. Nothing here can limit or approve.
- Settings: checkpoint size, model routing for Phase 1 and Phase 2, rate table, gate loop limit, skill version (upload, activate, roll back). Admins see counts and categories only. They never see question text or client files.

## 11. Security and quality requirements

- Forced RLS on every new table; add each to `tests/isolation/matrix/matrix.yaml` (probe every table and route). Cross-tenant tests for files, chunks, runs, checkpoints, skill acknowledgements and chat. Add the missing `-- reviewed:` comments to migration 0048 and review `runs_orphaned()` in 0050.
- Vector search scoped to tenant and account before retrieval, not filtered after.
- Per-tenant encryption keys, short-lived signed URLs, audit log for upload, run start, each checkpoint decision, download and delete.
- Tool and broker secrets never reach the browser or the model context.
- Prompt-injection tests on web content and file content.
- Idempotent tool calls: a retried step never pays twice.
- No secrets in the repo (move the test logins out of `web/.csp.mjs` and `web/.probe.mjs` before deleting them, A1.3).

## 12. Tests and acceptance (all must pass; report results)

1. Company-name run completes end to end with the real skill version, passes the gate, and builds every deliverable. Report units used, model tokens and tool spend for one real account. (Blocked until the skill package and keys exist; until then, prove it on the hermetic stack with the fake model and a fixture skill, and say so.)
2. File-mode run completes; facts are Internal with citations; zero broker calls when enrichment is off (assert on the ledger).
3. Checkpoint: a run pauses at the boundary; Continue, Continue 5 times, Auto-continue and Stop each behave correctly; a process restart while paused loses nothing; Stop still produces partial deliverables with tracker reasons.
4. Normal mode makes zero web and zero broker calls (assert); Deep mode runs the skill and records the skill version.
5. Scheduled refresh with auto-continue off pauses and notifies; with it on runs through.
6. No cap anywhere: a user can add accounts and run research without any limit coming from admin settings; grep confirms the removed features are gone (wallet, credit pool, account cap, plan limits, spend caps, throttle, library, seed upload, connector screens, `chat_limit_reached`).
7. Gate: a deliverable containing a data-tool name, a banned dash or a missing citation fails the gate and loops back.
8. Leak canary, prompt-injection file, infected or oversized file, and ambiguous-company file behave as specified.
9. Chat errors: with the model unavailable, the first question of a new chat stays on screen with a plain error and Retry (regression test for A6).
10. Frontend: Playwright with axe on every screen at 1280px and 375px: no axe violations, no horizontal page scroll, zero CSP console errors, mobile menu closes on Escape, every page has its own title, 404 page styled.
11. README, `docs/STATUS.md`, `TESTING.md`, `BUILD_LOG.md`, `docs/SCREEN_MATRIX.md` are corrected to match reality (A7): real model names, a real demo login seeded by a script (credentials in `.env.example` placeholders, not in docs), real test counts.
12. Full Python suite (including Postgres and isolation tests) and all web test files green, with no exclude lists, except documented exclusions in `docs/build/EXCLUSIONS.md` with a reason each.

## 13. How to work (this runs on a long-horizon agentic model)

1. Finish A1 and section 0 first. Then write a short build plan to `docs/build/PLAN.md` (replacing the root `PLAN.md`) and proceed without waiting.
2. Order: (a) snapshot commit and baseline; (b) fix the gate and the Word build so one run can complete end to end on the hermetic stack; (c) remove caps, wallet and the ledger's blocking (it unblocks everything after); (d) skill registry and executor; (e) meter and checkpoints; (f) Normal and Deep modes in chat, including the 503 fix; (g) file mode, consolidating the two file systems; (h) frontend screens and design fixes; (i) admin console; (j) deletions of the old graphs, legacy runs table, connectors and leftovers; (k) tests and docs.
3. Work autonomously for long stretches. Use sub-agents for independent work (for example frontend screens while the backend executor is built), then merge and re-run the whole suite.
4. Verify by running: start the stack, log in as the seeded demo user, run a company-name run and a file run, open each screen at desktop and phone width, and compare it against section 8. Fix what does not match before reporting.
5. Commit in small steps with clear messages. No secrets in commits. No outbound messages are ever sent (email, Slack and CRM write-back stay NotConfigured).
6. At the end report: what changed, test results with counts, the real cost of one run (or why it is blocked), what is not done, and every decision you need from me.

## Decisions to flag, not guess

- Kimi K3: confirm the provider account, key, price and that sending client data to Moonshot AI is acceptable before turning "Allow non-Claude models" on for any client.

- Checkpoint size. Default is 1,000 units as requested; confirm after seeing how often a real deep run pauses.
- Whether idle-access expiry stays at all.
- Whether audio transcription is in scope (needs a vendor and key).
- Data-grid component (proposed: `@tanstack/react-table`, see 9.6).
- Whether file-derived facts may ever be promoted into shareable output (default: no).
- Whether CRM (`connectors_client/crm_*`, `/v1/crm`) and the tenant's own model key (`/v1/chat/provider-key`) stay; neither is in this spec.
- Which embedding model to use for file chunks and the leak check (today a lexical hashing embedder).
- Whether to drop tables that hold real data (wallet ledger, usage counters, research_files rows) or archive them first.
