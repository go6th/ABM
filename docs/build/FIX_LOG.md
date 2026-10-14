# Fix log: repairs from the 2026-10-14 full folder audit

Source: `docs/audit/2026-10-14-full-folder-audit.md` (in the main folder). Work happens in this
worktree (`/home/sakib/abm-replica`, branch `feat/replica`). Postgres and isolation tests run on
the replica's own scratch databases (`ai_replica_test` for full runs, `ai_replica_adhoc` for single
modules), never on the shared `ai_test`.

## Decisions

| # | Decision | Why |
| --- | --- | --- |
| D1 | MAIN's uncommitted `src/agents/graph.py` change (deliver always COMPLETED) is not ported. | Prompt rule 2. The replica keeps PARTIAL when a High coverage row is incomplete; a test pins it (Phase 2.8). |
| D2 | MAIN's uncommitted `config/tracker_v1.yaml` change (`consent_required` allowed for COMPLETED) is not ported. | It does not fix a defect: it changes the completion policy so a run whose people rows are blocked on missing consent ends COMPLETED. That is the same loosening as D1. The gap stays visible: the row is Blocked with "People data consent is needed" and the run ends PARTIAL. |
| D3 | `DEMO_PASSWORD` leaves `.env.example`; the seed keeps reading it from the shell environment for the one command. | `.env.example` lists runtime settings the code reads; the seed is a dev script. The value is never committed. |
| D4 | Long SQL and message strings in `scripts/verify_wiring.py` carry a line-level `# noqa: E501` (outside SQL strings); lines inside SQL were wrapped instead. | A `#` inside SQL text is not a comment in PostgreSQL. |

## Phase 1: make the replica green

What changed:

1. Test failures (7):
   - Operator-name scan: `docs/build/BUILD_PROMPT.md` and `UNDERSTANDING.md` now say "the operator-only internal file". The scan was not weakened.
   - Telemetry contract: `api/routes/ask.py` and `replica/jobs.py` use `obs.logging.logger`.
   - Single write paths: owners declared for `client_settings`, `credit_entries`, `research_jobs` (`replica/credits.py`), `notifications` (new `replica/notifications.py`, the only writer), `question_categories` (`chat/store.py`). Writes that sat outside their owners were moved: Ask's thread and turn inserts into `chat/store.record_ask_turn`; `research_jobs` claim and run link into `replica/credits.py`; the hermetic engine's run row into `runs/lifecycle.record_hermetic_run`. Two documented column-group owners were added: `replica/schedules.py` for `accounts.refresh_cadence/next_refresh_at`, `replica/settings.py` for `memberships.last_active_at`.
   - Loops doc regenerated (`gen_loops.py`): line-number changes only.
   - Headers added: `tests/replica/__init__.py`, the `0054`, `0055`, `0056` down migrations.
   - Docstrings: 16 missing docstrings added; interrogate is back at 100%.
   - `DEMO_PASSWORD`: see D3.
2. ESLint (5 errors): `Reload` links use `next/link`; `Ask.tsx` follows the opened thread during render (React's "adjust state when a prop changes" pattern) and a separate effect only aborts the old stream.
3. Migration lint: the three SECURITY DEFINER functions were checked in the migrated database (owner `identity_authenticator`: NOLOGIN, NOBYPASSRLS, not superuser; `search_path = public, pg_temp`; EXECUTE revoked from PUBLIC and granted to `app_rw` only; they read only the one identity being signed in or reset, never tenant rows). `-- reviewed:` comments added; no function logic changed.
4. Other gates found failing on the way and fixed:
   - `ruff check` (66 long lines in `scripts/verify_wiring.py`, see D4) and `ruff format` (3 files).
   - `mypy` (12 errors in `replica/sheets.py`, `replica/schedules.py`, `runs/lifecycle.py`).
   - `bandit` (2 B608 in `replica/sheets.py`: the SQL is built only from allow-listed sheet definitions; annotated with the project's `# nosec B608 -- reason`).
   - `wiring_audit.py --check`: the scanner read `method:` only for one of its two `fetch` patterns, so `fetch(new URL("/v1/sso/logout"), {method: "POST"})` was recorded as a GET. Fixed the scanner and regenerated `docs/WIRING.md`.

Results: see the phase summary below.

## Phase 2: safety and correctness

### 2.1 Excel export

- `replica/sheets.workbook` now runs the client-file checks: dashes used as punctuation become commas and dash characters become hyphens; in a shareable workbook any cell that names a data tool, a credit count, a provider code or an internal reference is withheld ("Withheld for review", counted on the About sheet); text starting with `=`, `+`, `-`, `@`, a tab or a return gets a leading apostrophe and every text cell is stored as text. The finished file is scanned again as a whole (`exports.verify.scan_package` and the gate's dash rule) and refused with `422 export_blocked` if anything is left.
- Signals backed by a private (file-derived) fact are left out of shareable exports (they stay visible in the app).
- The mail and drive sheet is exported only with the caller's own items, its sheet is named "Private to you", the About sheet and the file title say so, and it is not scanned as shareable (the user's own words stay verbatim).
- Gate defect found and fixed: `documents.gate.client_text` did not decode XML character references, and openpyxl stores an em dash as `&#8212;`, so the live pre-delivery gate never saw dashes inside workbooks. It now decodes them; a test pins it.
- Tests: `tests/replica/test_sheet_cells.py` (database free) and `tests/replica/test_sheet_export.py` (planted tool name, formula cell, dash, private-fact signal, private sheet).

### 2.2 Nightly spend invariant job

- `python -m account_intelligence.worker spend-invariants` now exists (the command Terraform already runs). It checks every ledger rule of every tenant (`worker/nightly.py`): the paid-call ledger of each run created in the last two days (`spend.jobs.spend_invariants`, unchanged) and the credit pool ledger of each tenant with credit activity in the last 40 days (`replica.credits.ledger_problems`: open jobs hold their estimate, ended jobs released what they held and settled at most that, `credits_used` equals what was settled, waiting jobs hold nothing, a library add moves nothing, no period holds a negative amount). Tenants come from a new identifier-only lookup `tenants_with_credit_activity` (migration 0057).
- Exit codes: 0 nothing broken, 1 a broken rule (each printed as `tenant:rule:job`, identifiers only), 2 bad arguments or environment, 3 the check could not run (never a silent pass). The dead `spend.jobs.main` entry point was removed.
- Tests: `tests/worker/test_nightly.py` (exit codes; a planted broken ledger is flagged and a clean tenant is not).

### 2.3 One credit truth

Default taken from the prompt: the replica's monthly pool and `credit_entries` are the single source of credit truth (decision D5).

- Migration 0057: every run belongs to one research job (`uq_research_jobs_run`); `library_add` is a job kind (zero credits); `research_jobs.stop_reason`; the wallet tables become read-only history (`REVOKE INSERT, UPDATE ... FROM app_rw`; nothing is dropped).
- `runs.lifecycle.create_run` no longer touches `wallets`: a run either carries out a job confirmed earlier (`CreditJob(job_id=...)`) or reserves its own job on the pool in the same transaction (`CreditsShort` becomes `InsufficientCredits`, 402 as before). The run's `reserved_credits` are the job's credits and its `budget_usd` is those credits in dollars through the rate table, never above the configured run budget (D6).
- `settle_once` settles the run's job once (`replica.credits.settle_run`): credits used = ceil(real spend / credit value), where real spend is the paid data calls plus the run's model calls (`llm_calls.cost_usd`), never more than reserved; the rest is released. A FAILED run (platform fault) or `refund_all` charges nothing (D-04 kept). The person who asked is notified. A run from before the single ledger (no job) settles nothing.
- Routes pass the person: `POST /v1/runs` (new research), file runs (reading the client's own files: 0 credits; the linked public-sources run reserves the `file_public` estimate).
- `metering.reconcile` counts a run as settled when its job has ended (or from the wallet history).
- Tests rewritten to the pool ledger (same intent: one transaction, reserve once, never overspend under concurrency, settle once, platform fault charges nothing): `tests/runs/test_lifecycle_db.py`, `test_lifecycle_edges.py`, `tests/integration/test_create_run.py`, the system tests' money helpers (`tests/system/world.py`, `hermetic_stack.py`) and their callers, `tests/spend/test_kill_resume.py`, `tests/phase3/test_ws7_metering.py`. New: the application role cannot write the wallet tables.

### 2.4 Credit cap per pull stops a live run cleanly

- The credits confirmed on the pool are the run's cap, so runs are created with `cap_required = false` (no second approval, no "Continue?" interrupt) and the run's dollar budget is the reservation (D6).
- ABM graph: when a paid call is refused because the run used its credits (`cap_reached`), the Pull agent marks the row Blocked `budget_cap` and makes no further paid call for later rows (`PullAgent.capped`); targeted pulls do the same. The run keeps going with what it found (interpretation, distill, files) and the graph factory ends it BUDGET_EXCEEDED (`budget_cap`); the job is settled with `stop_reason = credit_cap_reached`. The tracker label for `budget_cap` now reads "Stopped at the credits confirmed for this research".
- Workflow v1 parallel phase: a unit the credits stopped no longer needs an approved cap version to be counted; the phase sets `budget_exhausted` so the run ends BUDGET_EXCEEDED with every row reported.
- Tests: `tests/system/test_parallel_cap_pause.py` rewritten (a parallel run at its credits stops cleanly and never pauses; job marked `credit_cap_reached`), `test_failure_injection.py` (budget cap marks the job), `test_credit_cap_run.py` and `test_whole_run.py` (no approval wait, ever).

### 2.5 Per-user account cap

- `runs.lifecycle.add_account(..., added_by=user)` is the one place a new account is inserted; a person's new account checks `replica.settings.ensure_account_room` under a lock on the client's settings row and records `accounts.added_by`. `create_run` uses it, so new research, file analysis (`/v1/documents/confirm-company`), `POST /v1/accounts` and `POST /v1/runs` all enforce the cap; the library add (Phase 4) will too.
- A global handler answers `409 account_cap_reached` with the design's words: "You have used all N accounts. Ask your administrator to raise the cap."
- Tests: `tests/replica/test_account_cap.py`.

### 2.6 Rate limits

- One Redis setting: `REDIS_URL` (`redis://` locally, `rediss://` for Memorystore with TLS) for the sign-in limiter, the provider limiter and the research cache index. `REDIS_HOST` is gone from the registry, the container, the template and Terraform (which now sets `REDIS_URL = rediss://<host>:6379/0`). Readiness reports `rate_limit_store: redis` or `in_process`.
- Test: `test_rate_limits_and_readiness_read_the_one_redis_setting`.

### 2.7 Operator Google OAuth secret

- The web app already read the operator settings from the environment or from a `NAME_FILE` path, and Terraform injects the secret from Secret Manager. `web/.env.example` now lists the names with no values and documents the `_FILE` form; a unit test pins reading the secret from a file. MAIN's `web/.env.local` was not opened, copied or read. Owner action in the final report.

### 2.8 Partial on incomplete

- `test_a_run_with_a_high_row_unset_never_ends_completed` (lifecycle: asked to end COMPLETED with a High row never reported, the run ends PARTIAL and the row is swept with a reason) and a source guard on the ABM graph's deliver step.

### Decisions added in Phase 2

| # | Decision | Why |
| --- | --- | --- |
| D5 | The pool and `credit_entries` are the one credit truth; wallets stay read-only for history. | Prompt default. Nothing dropped. |
| D6 | A run's dollar budget is `min(configured run budget, credits x credit value)`, at least 0.0001 USD (the column's smallest positive value, for 0-credit file reading). | The reservation is the cap; an existing safety ceiling is never relaxed; the database requires a positive budget. |
| D7 | Runs are created with `cap_required = false`. | Confirming credits replaces the per-run cap approval (REPLICA_PROMPT: no checkpoints). The credit manager's approval paths stay in the code until Phase 6 removes the routes and UI. |
| D8 | A FAILED run (platform fault) is charged nothing even though it spent. | The earlier rule D-04, unchanged. |
| D9 | `documents.gate.client_text` decodes XML character references. | A real gate defect: dashes in workbooks went undetected. |

## Phase 3: make one real run possible

### 3.1 Model wiring

- `UnconfiguredLlm` stays when no key is set. Readiness now says `llm: not configured (ANTHROPIC_API_KEY)` (or `fake (LLM_FAKE)`, or `present`), never a value.
- `GET /v1/me` has `model_ready`; when it is false the workspace shell shows one plain line: "Answers need the AI model to be set up by your administrator." (Ask still answers from the workspace with the local answerer, as REPLICA_PROMPT allows.)
- `compose.replica.yml` (new, `make replica-up`): the replica as its own compose project on its own ports (web 3010, API 8090, worker 8091, Postgres 5433, Redis 6380), with `LLM_FAKE=hermetic`, `CONNECTORS=hermetic` and `RESEARCH_ENGINE=graph` as the documented defaults. Production refuses both fakes (existing rule for the model, new rule for the providers).

### 3.2 Production research engine

- `replica/jobs.GraphEngine` (the `graph` engine, staging and production default): a confirmed job (new research, refresh with scope jobs / people / full / topic, scheduled refresh) becomes an `account_runs` run through `runs.lifecycle.create_run` with `CreditJob(job_id=...)` (the job's reserve is the run's credits; no second reservation), its mode and settings pin are recorded, and slice 0 goes to the worker. The job is settled when the run ends (Phase 2.3): real costs through the rate table, released untouched if it fails before the run exists.
- Scopes map to the agent graph's rows (`config/research_scopes.yaml`); rows outside a scope are reported "No search was planned for this row in this run" (`not_planned`), never left out. A topic refresh searches with the user's own words in place of the row label. Follow-up pulls outside the scope are not made.
- A job's run always runs on the ABM agent graph (`uses_abm`), as does the public-sources run linked to a file analysis; other runs keep the `abm_agents` flag rule.
- A job's call cap is sized from its plan (rows in scope x phrasings x 2), never below the configured cap (D12); its dollar budget is still its credits.
- Readiness reports `research_engine` (for example `abm_graph (skill package not installed)`). The `skill` engine refuses with a plain reason: the skill executor of BUILD_PROMPT section 4 is designed against the owner's skill package, which is not installed (blocked on owner).
- Tests: `tests/replica/test_engine.py`.

### 3.3 Word files in the cloud

- The python-docx renderer is the default everywhere (`app/container.py`, `exports/wiring.py`), and Terraform sets `EXPORT_RENDERER = "python-interim"` explicitly (D10). `EXPORT_RENDERER=node` switches back once the Node library is ported.

### 3.4 Production delivery

- `builders_not_configured` is gone from the agent graph's path: a run wired without a builder gets the platform's export builder. The internal audience now builds the run's Internal Only file with `documents.internal_doc` into `tenants/<t>/internal/<run>/`, recorded in `internal_deliverables`, labelled "Internal: from this research run" for a company run; it is never scanned or released as a client file. The internal-file download reads it from either store.

### 3.5 People source

- BitScale's documented API (https://docs.bitscale.ai/ingredients/bitscale-api-reference.md, base `https://api.bitscale.ai/api/v1`, `X-API-KEY`) has workspace, grid listing, grid run, run status and key rotation endpoints, and no people-search endpoint; people search exists only as a grid each customer builds, whose column ids are specific to that workspace. Its published OpenAPI file is an unfilled template. Decision D11: keep it disabled. Readiness says `provider.people: disabled (needs the owner's people-search grid; people rows use the org source and web search)`. The agent graph's people rows already route to the org source (Sumble) and web search; BitScale was first only for title search and profile enrichment, which no planned row uses.

### 3.6 One gate

- `documents.gate.PreDeliveryGate` gained the checks the orphaned roadmap gate only declared: `deliverable_set` (the five client files of a company run), `rows_dropped` (every row the builder recorded in a file's manifest is in it, once and in order in a Word file), `third_party_domains` (no data provider's host in a client file). `src/gate/` (14 NotConfigured ports) and its unit tests are deleted; the import contracts, packaging and `docs/TRACEABILITY.md` point at the live gate.

### 3.7 Proof

- `CONNECTORS=hermetic` (new, dev and test only): synthetic replies from the contract cassettes (copied to `config/hermetic/providers/`) for any account, through httpx mock transports with no DNS query; nothing leaves the process.
- `tests/system/test_offline_end_to_end.py`: with the hermetic model and providers, a company run started as a credit-confirmed job and a file analysis with public sources both finish through the real API, worker endpoint, database, builder and gate: the gate passes, the five client files are built, the job is settled, and the file run produces its Internal Only file.
- Defect found on the way (D13): facts backed only by aggregator feeds (job, news and technology feeds) were graded Probable by the engine, but the builder refuses any such fact that is not a Lead, so every run with feed data would have failed to build. The skill's rule is "aggregator only is a Lead". New precedence rule `aggregator_only` (after `unread_match`, before `engine_a_or_b`); `secondary_only` (no first-party source, which may still be Probable from a reputable outlet) is unchanged. Two grading tests that used a feed to test other rules now register the feed as a non-aggregator for that test; a new test pins the rule.

### Decisions added in Phase 3

| # | Decision | Why |
| --- | --- | --- |
| D10 | Python Word renderer by default everywhere. | The Node renderer is not ported; cloud runs ended PARTIAL. |
| D11 | BitScale stays disabled; people rows use the org source and web search. | No documented people-search API (see 3.5). |
| D12 | A job's call cap = rows in scope x phrasings x 2, at least the configured cap. | The workflow's 60-call cap stopped a full agent-graph run long before its credits. |
| D13 | Aggregator-only findings are Leads. | Grading and the builder disagreed; the skill and the builder say Lead. This touches D-01 (grade mapping, open): the owner should confirm. |
| D14 | `CONNECTORS=hermetic` for the offline proof instead of literally "connectors off". | With no connector runtime the agent graph cannot run at all; synthetic providers exercise the real ledger, grading, builder and gate with no outside call. |

## Phase 4: finish the missing replica features

| # | Item | Status | Where |
| --- | --- | --- | --- |
| 4.1 | Add account (`/accounts/add`, static route reserved before `[domain]`; three ways; ready-made list; cap message) | Fixed | 42dfb99 |
| 4.2 | Mail and drive, per user and read only (Gmail, Outlook, Google Drive, OneDrive, SharePoint); sync matches items to the user's accounts; Disconnect revokes and deletes; "Not available yet" without OAuth apps; dev demo shows Connected | Fixed (live OAuth blocked on the owner's apps) | 3a6a0c7, 17fb7c4, 58bc5b4 |
| 4.3 | 6th Library: available list, add from library (no credits, counts toward the cap), admin A5 upload, check and publish | Fixed | 5201d99, 0bdfe49 |
| 4.4 | New research by company on credits (estimate, confirm, reserve, start) | Fixed | 42dfb99 |
| 4.5 | File research costs in credits (0 without public sources; "About N credits, confirmed before it runs") | Fixed | 42dfb99 |
| 4.6 | File analysis screen (`/accounts/{d}/files`, `GET /v1/accounts/{domain}/file-analysis`) | Fixed | 42dfb99 |
| 4.7 | Ask streams checked sentences; grounding reads the tracker, uploaded files (Internal) and previous deliverables | Fixed | 4bd2632 |
| 4.8 | Coverage tracker (33 rows) in the Deep dive rail | Fixed | 2932789 |
| 4.9 | Admin console A1 to A7; Add client with a copyable invite link | Fixed | 0bdfe49, 359636a |
| 4.10 | View as a client (grant, banner, read only, audited, no content) | Fixed | 5b16ff9 |
| 4.11 | Access state: idle warning and expiry, Terminate and Re-enable on every sign-in method and API call | Fixed | 0bdfe49, c70cf3a |
| 4.12 | Nightly isolation check (structure, tables, routes; records and the console pill) | Fixed | 86e1b00 |

Also in this phase (owner requests of 2026-10-14): the sign-in page as PDF page 1, the admin
sign-in, Keep me signed in (password and SSO), SSO discovery by email domain
(`POST /v1/sso/discover`, migration 0062), and Ask without sample text (748a9b7, 36a569d,
07f0ff4, d8233ca).

### Checks after Phase 4 (2026-10-14, replica test database on port 5433)

- Python, full suite with PostgreSQL and isolation (`pytest -m "not eval"`): **3,953 passed, 13
  skipped, 2 failed**. Both failures were fixed in the commit that records this phase:
  - `test_sessions_expire_rotate_and_revoke`: two sessions created at the same fake-clock
    instant were ordered by `created_at` alone, so their order was arbitrary. The query now
    breaks the tie (`revoked_at NULLS LAST`).
  - `test_one_retry_owner_per_failure_class_w6`: `docs/architecture/loops.md` named line 247 for
    the mail and drive sync loop after 58bc5b4 moved it to 246; regenerated.
- `check_route_catalog.py`, `check_guarantees.py` (419 tags, 0 dangling), `check_routing_governance.py`,
  `check_comments.py`, the skill's migration lint (122 files) and the RLS catalog check: pass.
- Web: typecheck, lint (0 errors), 48 unit tests and the production image build pass.
- Playwright with axe on the replica stack: the public screens (sign-in, admin sign-in, forgot,
  reset, verify email, invite) and Ask at 1360 and 375 pass with no violations, no console or
  CSP errors and no sideways scroll. The signed-in screen spec needs `DEMO_PASSWORD`, which was
  not set in this shell, so the rest of the workspace screens were not re-run in this phase.

### Still open after Phase 4

- Live OAuth for Mail and drive, the staff Google sign-in and real research runs need the
  owner's inputs (Phase 8).
- Phase 4B (`docs/build/PHASE_4B_PLAN.md`) replaces the account cap and user-facing credits
  with per-user research and chat limits, and makes every user's workspace private.
