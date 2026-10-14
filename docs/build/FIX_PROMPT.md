# Fix prompt: repair everything the 2026-10-14 audit found

You are working on the Account Intelligence project. Read `docs/audit/2026-10-14-full-folder-audit.md` (in `/home/sakib/ABM Generator`) first: every item below comes from it, with file and line evidence. Verify each finding before you change code; the tree may have moved since the audit.

## 0. Rules

1. **Where to work.** All work happens in the replica worktree `/home/sakib/abm-replica` on branch `feat/replica`. It is the copy that follows the owner's latest product decision (`docs/build/REPLICA_PROMPT.md`). Do not edit files in `/home/sakib/ABM Generator` (another session works there), except where a step below says so.
2. **Do not port MAIN's uncommitted change to `src/agents/graph.py` lines 647-652** (it makes `deliver` always mark runs `completed`). Keep the replica's behaviour (PARTIAL when High coverage rows are incomplete). Do port MAIN's uncommitted `config/tracker_v1.yaml` change only if you can explain what it fixes; record the decision in `docs/build/FIX_LOG.md`.
3. **Commits.** Small commits on `feat/replica`, your own files only, clear messages. Never push, never deploy, never send anything outward.
4. **Secrets.** Never print, log or commit a key, password or token. Report key names only.
5. **Order.** Do the phases in order. After each phase run the full checks in section 9 and write the result in `docs/build/FIX_LOG.md` (what changed, test counts, what is still open).
6. **Owner-only items** (section 8) cannot be done by you. Build everything up to the point where only the owner's input is missing, make the gap visible in plain words in the UI and readiness checks, and list it in the final report. Never fake a working feature.
7. **Decisions.** Where this prompt gives a default, use it and record it. Stop and ask only before something that cannot be undone (dropping a table that holds data, rewriting git history).

## Phase 1: make the replica green

1. Fix the 7 failing Python tests:
   - `tests/agents/test_conversions.py::test_conversion_operator_only_file_is_internal_only`: the forbidden operator name appears in `docs/build/BUILD_PROMPT.md:174` and `docs/build/UNDERSTANDING.md:76`. Reword those lines (say "the operator-only internal file") rather than weakening the scan.
   - `tests/obs/test_contract_and_pure.py::...[check_telemetry.py]`: replace `getLogger` in `api/routes/ask.py:36` and `replica/jobs.py:32` with the project's `obs` logging API.
   - `tests/system/test_single_write_paths.py::test_each_table_is_written_only_by_its_owner`: declare owners for `client_settings`, `credit_entries`, `notifications`, `question_categories`, `research_jobs` the way existing tables do, and make sure each table really has one writing module.
   - `tests/system/test_static_guards.py::test_one_retry_owner_per_failure_class_w6`: regenerate the loops doc with `gen_loops.py` and review the diff.
   - `test_every_file_has_its_header_and_every_guarantee_tag_resolves` and `scripts/check_comments.py`: add headers to `tests/replica/__init__.py` and the `0054`, `0055`, `0056` down migrations.
   - `test_interrogate_docstring_coverage_is_complete`: add the missing docstrings (do not lower the 100% bar).
   - `tests/system/test_wiring_contract.py::test_env_example_lists_exactly_what_the_code_reads`: remove `DEMO_PASSWORD` from `.env.example`, or make the seed script read it from the environment (preferred: the seed reads it, and the value is never committed).
2. Fix the 5 ESLint errors: use `next/link` instead of `<a>` for internal routes in `app/(client)/accounts/page.tsx:54` and `app/(client)/page.tsx:31`; move the state update out of the effect in `components/ask/Ask.tsx:64`.
3. Migration lint (skill script `lint_migrations.py`): review each SECURITY DEFINER function in `migrations/0048_password_reset.sql:6,18` and `migrations/0053_replica.sql:180` (and the down file). Confirm each sets a safe `search_path`, is owned by the right role, grants EXECUTE narrowly and cannot read across tenants; then add the `-- reviewed: <reason>` comment. Never edit an applied migration's logic: if a function is unsafe, fix it in a new forward migration.
4. Run the full suite including Postgres and isolation tests **on the replica's own test database** (never the shared `ai_test`), plus web typecheck, lint, unit tests and a production build. Everything must pass before Phase 2.

## Phase 2: safety and correctness fixes

1. **Excel export bypasses the client-safety checks.** `replica/sheets.py::workbook` must run the same checks as other client files: the client-output scan (no data-tool names, credit counts or internal references), banned-dash check, and formula neutralising (cells starting with `=`, `+`, `-`, `@` are escaped). Private rows (mail and drive, file-derived Internal facts) never enter a workbook that could be shared; the "From your mail and drive" sheet exports only to its owner and is labelled private. Add tests with a planted tool name, a formula cell and a private row.
2. **Nightly spend invariant job is broken.** Terraform (`infra/stack/main.tf:352,390`) runs `python -m account_intelligence.worker spend-invariants`, but the CLI requires `--tenant-id/--run-id` and exits 2 (`worker/__main__.py:24-29`). Add a real all-tenant command that checks every ledger invariant and exits non-zero only on a broken invariant; test it.
3. **One credit truth.** There are two ledgers: `wallets`/`wallet_ledger` (old runs) and `credit_entries` (replica pool). Default: make the replica's monthly pool and `credit_entries` the single source of credit truth; route every credit movement (new research, refresh, scheduled refresh, file research with public sources, library adds at zero cost) through it; stop reading `wallets` for decisions; keep the old tables read-only for history. Do not drop them (they hold data). Add an invariant check (pool used = sum of settled entries) to the nightly job.
4. **Credit cap per pull** must stop a live run cleanly when reached (not only clamp the estimate): stop paying, keep what was found, build partial deliverables, mark the job and tracker rows with the reason.
5. **Per-user account cap** must be enforced on every way an account is added (new research, from library, file analysis creating an account), not only shown. Return a clear error and show the design's message.
6. **Rate limits:** readiness reads `REDIS_HOST` while compose sets `REDIS_URL`, so limits run in process. Make both read the same setting and use Redis when available; readiness reports `redis`.
7. **Operator Google OAuth secret** is in MAIN's `web/.env.local` (untracked). Make the web app read operator OAuth settings from the environment or a secret file path, document the names in `web/.env.example` without values, and tell the owner in the final report to move the value to a secret manager and rotate it if it was ever shared. Do not read, copy or print the value.
8. **Uncommitted MAIN `graph.py` change:** confirm the replica keeps the partial-on-incomplete rule and add a test that a run with a High row unset cannot end COMPLETED.

## Phase 3: make one real run possible

1. **Model wiring.** Keep `UnconfiguredLlm` when no key is set, but make the gap explicit: readiness shows `llm: not configured (ANTHROPIC_API_KEY)`, and the UI shows one plain message ("Answers need the AI model to be set up by your administrator") instead of failing silently. In dev and test, make `LLM_FAKE=1` the documented default in `compose.yml` for the replica stack so the app works end to end on fakes; production refuses fakes (existing rule).
2. **Production research engine.** `replica/jobs.py` `SkillEngine` raises `EngineNotConfigured`. Bridge it to the existing run machinery: a credit-confirmed job (new research, refresh with scope jobs / people / full / topic, scheduled refresh) creates an `account_runs` run through the existing lifecycle and executes it with the ABM skill executor described in `BUILD_PROMPT.md` section 4 when the skill package is present, or with the existing Workflow v1 / ABM graph when it is not. Map the scopes to the steps each needs. Credits are reserved on confirm, settled from the ledger's real costs through the rate table, and released if the run fails before spending.
3. **Word files in cloud.** `exports/word/renderer.py:68` defaults to the `node` renderer, which is NotConfigured, so cloud runs end PARTIAL. Make the working Python renderer the default everywhere until the Node renderer is ported, and set `EXPORT_RENDERER` explicitly in Terraform.
4. **Production delivery.** Remove `builders_not_configured` (`agents/production/delivery.py:56-62`): wire the export builder in every environment. Wire the internal-only builder (`delivery.py:55`) using `documents/internal_doc.py`.
5. **People source.** `connectors/providers/p_people.py` (BitScale) raises on every operation. Implement it against BitScale's documented API behind the existing connector interface and ledger (look up the API; do not guess endpoints), with the one-time tenant consent from `BUILD_PROMPT.md` section 4.7. If its documentation is not available, keep it disabled with a clear readiness message and let Sumble fill the people rows; record the decision.
6. **Gate.** Make `documents/gate.py::PreDeliveryGate` the single gate. Port any check from the orphaned `src/gate/pipeline.py` (row-drop check, document checks, third-party domain scan) that the live gate lacks, then delete `src/gate/` and its 14 NotConfigured ports.
7. **Prove it:** with `LLM_FAKE=1` and connectors off, a company run and a file run complete end to end on the replica stack, pass the gate and produce all five deliverables. Add this as an automated system test.

## Phase 4: finish the missing replica features

Follow `REPLICA_PROMPT.md` section 2 and the design files in `docs/design/claude-design/` for look and behaviour. Fix the two broken links first.

1. **Add account** (`06-add-account`): route `/accounts/add` (fix the `[domain]` catch: reserve `add` and other static paths), the three ways, the ready-made list, cap message.
2. **Mail and drive** (`08-mail-and-drive`): route `/mail-and-drive`, Mail and Drive cards, "How it is used". Per-user, read-only connections: Gmail and Outlook, Google Drive, OneDrive and SharePoint. Use the per-user columns in `0053`; the existing routes are tenant-level, so rework them to per-user; tokens encrypted per tenant and never sent to the browser or model; a sync job matches items to the user's accounts; Disconnect revokes and deletes. Until the owner supplies OAuth apps, the cards show "Not available yet" and tests use a fake OAuth provider. Make the demo user show Connected in dev so the Deep dive count, the answer panel and this page agree.
3. **6th Library:** `GET /v1/library/available`, `POST /v1/accounts/from-library` (copies the published workspace into the tenant, no credits, counts toward the cap), and admin A5 (upload zips, detect the five deliverables, leave out the internal-only file, run the client-ready check with the gate's checks, publish to chosen clients). Library content is platform-level, never tenant data, until copied.
4. **New research by company** on credits: estimate, confirm, reserve, start, replacing the old checkpoint wording in `research/new/page.tsx`.
5. **File research costs in credits**, not dollars: 0 when public sources are off; "About N credits, confirmed before it runs" when on; reserve on confirm.
6. **File analysis screen** (`10-file-analysis`) as its own route, using `FileAnalysis.tsx`; replace the old FilesTab at `/accounts/{d}/files`.
7. **Ask:** stream the answer text incrementally (keep citations valid as text arrives); let grounding read the tracker, uploaded files (Internal facts, labelled) and previous deliverables as well as facts.
8. **Tracker:** show the 33-row coverage tracker to users on the account page (a "Coverage" item in the Deep dive rail using the sheet component), with status and reason per row.
9. **Admin console:** A1 (KPIs, questions per week, categories read from `question_categories`, "Not using it"), A2 (Add client with a copyable invite link, credit bars, statuses), A3 (Client detail: account cap, idle expiry, monthly pool, features, fastest schedule, publish from library, save), A4 (per-user list with Terminate / Re-enable, plus an operator users endpoint), A6 (credits by purpose and the runs table), A7 (Platform defaults as designed: caps, pool, fastest schedule, skill version, credit cap per pull, Model split: Split mode / Single model, safeguards, admins). Replace the current Settings page (checkpoint size, phase model flags).
10. **View as a client:** screen with a visible banner, read-only, only with the client admin's support grant, never question text or file contents, audit-logged.
11. **Access state:** idle-expiry job (warn 3 days before, end access, keep history), Terminate and Re-enable, enforced on every sign-in method and every API call, not only password sign-in.
12. **Nightly isolation check:** a job that probes cross-tenant reads for every tenant table and route (reuse the isolation suite's probes against a dedicated check tenant pair), writes `isolation_checks`, and feeds the admin pill. Failures alert the operators.

## Phase 5: wire what is built but unused

1. **Scheduler triggers:** Cloud Scheduler jobs in Terraform for `/tasks/schedules-tick`, the nightly spend invariant, the isolation check, idle expiry, retention and the alert digest. Locally, a small scheduler service in compose runs the same ticks.
2. **Refresh:** use probe-then-escalate (`refresh/probe.py`, `escalate.py`, `debounce.py`) for scheduled refreshes so a quiet account costs little; write `deltas`/`delta_items` on every refresh (`refresh/records.py`), and make "What changed since" and notifications read from deltas.
3. **Alerts:** wire `alerts/engine.py`, `outbox`, `digest` to deltas; in-app notifications are the delivery channel the design shows. Slack, webhook and email stay NotConfigured with clear readiness messages until the owner supplies them; outbound webhooks are signed when enabled.
4. **Reconciler:** re-send QUEUED runs older than 60 seconds and settle abandoned ones; run the sweeper in the deployed worker (set `SERVICE_ROLE=worker` on the worker service in Terraform) and as a scheduled tick.
5. **Tracing:** call `obs.setup.configure()` at API and worker start-up with an exporter chosen by settings (none locally, the cloud exporter in staging and production).
6. **Audit:** wire hash-chain verification as a nightly job; plan the 7-year locked archive in Terraform (bucket with retention lock), applied when the owner deploys.
7. **Privacy jobs:** wire DSAR, erasure, suppression and retention behind settings. Retention periods stay at the proposal values and are labelled "pending legal review"; do not delete production data automatically until the owner confirms the policy.
8. **Private retrieval:** wire `account_intelligence/rag/` for Ask over uploaded files and mail and drive items, scoped by tenant, account and (for mail and drive) user before retrieval. Keep `HashingEmbedder` as the default until the owner picks an embedding model; make the embedder a setting.
9. **Startup self-check:** at start-up, refuse to run if the database role can bypass RLS or owns the tables (move the check from `scripts/verify_wiring.py` into start-up).
10. **Per-tenant keys:** implement envelope encryption per tenant for stored secrets and private file objects using `tenants.kms_key_arn` (local: a development key provider; cloud: KMS in Terraform), and crypto-shredding on tenant offboarding. Mark the cloud part "applies on deploy".
11. **Research graph shape:** add the plan and gap loop (at most two extra rounds) and a distinct verify step to the ABM graph, matching Architecture v2, unless the skill package defines a different flow (then follow the skill and record why).

## Phase 6: clean up contradictions and leftovers

1. Remove leftovers that contradict `REPLICA_PROMPT.md`: checkpoint "Continue?" routes and UI (`/v1/runs/{id}/approve`, `/cap/*`, `/cap/pauses/*`, `RunSteps.tsx:144-162`, "Checkpoint size"), model-choice leftovers (`/v1/models` if unused by the design, `/v1/chat/provider-key`, `ProviderKey.tsx`, phase model flags), redirect-stub pages (`/alerts`, `/chat`, `/downloads`, `/runs`, `/usage`, `/settings/*`, operator `spend`, `cost`, `tenants`, `support`), dead components, and the admin comment "No caps of any kind anywhere". Remove each from the route catalog, OpenAPI snapshot, generated client and tests.
2. Record the owner's label changes in `REPLICA_PROMPT.md` section 2 (Search: off/on, "+", "Accounts", the fourth "topic" refresh scope) so the spec matches the build.
3. Remove stray tracked files at the repository root after checking nothing uses them: `run_sql.py`, `screenshot_all.*`, `verification-report.txt`, root `AUDIT.md` and `BACKEND_AUDIT.md` (move useful content to `docs/audit/`), `ABM_Skill_Readers_Guide_v2.docx` (move to `docs/sources/`, which is gitignored). Add generated folders to `.gitignore`.
4. `src/security/pentest_gate.py::beta_gate()` returns True unconditionally: make it read a real recorded result or delete it.
5. Rewrite `TECHNICAL_DOCUMENTATION.md` (and note that the PDF in Downloads is outdated) from the code as it now is: the 22 wrong statements listed in audit section 8, plus the research engine, credits, library, CI, infra and evals. Correct `README.md`, `docs/STATUS.md`, `TESTING.md` with real numbers.

## Phase 7: CI and deploy readiness (no deployment)

1. Make CI pass on `feat/replica`: lint and types, Playwright e2e (rewrite specs for the replica screens, with axe), image builds, the eval workflow (pin `upload-artifact` to a SHA; make the routing-governance step behave the same in CI as locally).
2. Live evals: keep the live step off, but make it skip with a clear "needs ANTHROPIC_API_KEY and golden accounts" status instead of failing every nightly run.
3. Prepare deploy without deploying: Terraform validates and plans against example tfvars; write `docs/DEPLOY_CHECKLIST.md` listing every GitHub environment, variable and secret, the state bucket, tfvars values, OAuth apps and keys the owner must supply, in order. Record the GCP-versus-AWS divergence from skill reference 10 as an ADR.
4. Branch protection and pushing are owner actions: list them in the checklist.

## Phase 8: what only the owner can supply (do not attempt)

1. The ABM research skill package (`Three_step_Opus_Fable_account_research_with_veeva_and_Mulesoft.skill`), to be placed at `skills/abm-account-research/`.
2. `ANTHROPIC_API_KEY` and the data-provider keys (`P_SEARCH_API_KEY`, `P_SCRAPE_API_KEY`, `P_SIGNALS_API_KEY`, `P_SIGNALS_API_TOKEN`, `P_PEOPLE_API_KEY`, `P_ORG_API_KEY`).
3. Google and Microsoft OAuth apps for Mail and drive; Slack or email settings for alerts.
4. Cloud project, region, billing, domain, Terraform state bucket, GitHub environments and secrets.
5. Decisions: credit value, people source, retention and DSAR policy (legal), embedding model, real golden accounts for evals.

## 9. Checks after every phase

- Full Python suite including Postgres and isolation tests on the replica's own test database; the database-free suite; web typecheck, lint, unit tests, production build.
- `scripts/check_route_catalog.py`, `check_guarantees.py`, `check_routing_governance.py`, `check_comments.py`, the skill's `lint_migrations.py`, the RLS catalog check.
- Playwright with axe on every built screen at 1360px and 375px: no violations, zero CSP console errors, no horizontal scroll.
- Start the replica stack, sign in as the demo user, click through every screen and every control, and confirm no 404, no "not found", no silent failure.

## 10. Final report

At the end, report in `docs/build/FIX_LOG.md` and in chat: each audit item with Fixed / Partly fixed / Blocked on owner / Not done and why; test and check results with counts; screens and features now working; the exact list of owner inputs still needed (section 8); and any decision you made with its reason.
