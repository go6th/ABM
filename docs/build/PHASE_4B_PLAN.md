# Phase 4B plan: model choice, files at any size, per-user limits, resumable research, full downloads, per-user isolation, minimal UI

Owner prompt of 2026-10-14 ("Build prompt: model choice, file analysis at any size, per-user caps,
resumable research, full downloads, strict isolation, minimal UI"). It runs after FIX_PROMPT
Phase 4 and before Phases 5 to 7. Where it overlaps Phase 5 (private retrieval, scheduler,
refresh) the work is done here and marked done there. Where it disagrees with REPLICA_PROMPT.md,
BUILD_PROMPT.md or FIX_PROMPT.md, it wins; every reversal is recorded in `PLAN.md` and
`REPLICA_PROMPT.md` section 0 as an owner decision dated 2026-10-14.

Money rule: no live run that calls a paid model or data provider without the owner's "go" in chat
for that run. Every test uses the fake model (`LLM_FAKE=hermetic`) and the hermetic providers
(`CONNECTORS=hermetic`).

## 1. What exists today

### Models

- Live path: `account_intelligence/platform/llm.py` `AnthropicLlm` serves a **tier** (`planner`,
  `extractor`, `classifier`, `synthesis`, `narrative`, `judge`) with the route resolved from the
  active arm of `config/models.yaml` (`load_routes`). The active arm is `cost_saving`: Opus 5.5
  plans and verifies, **Sonnet 5.5** extracts and classifies, Fable 5.1 writes synthesis and
  narrative. The owner's description ("Opus pulls and extracts, Fable analyses") is the
  `automatic` arm.
- `src/llm/tasks.py` `LEGACY_TIER` maps every task kind to a tier; `workflow/deps.py`
  `RunDeps.model` calls the legacy port (`_legacy_model`, with the run's own reply payloads and
  the shared research cache `llm/reply_cache.py`) unless `MODEL_ROUTER=on`.
- The D-48 router (`src/llm/port.py`, `src/llm/config.py`, `config/model_routing.yaml`) is off
  (it needs a benchmark report, D-51). Its `fast` candidate is the stale
  `claude-haiku-4-5-20251001`. `with_model_split` implements the admin "Model split".
- Ask (`api/routes/ask.py`) picks its model with `account_intelligence/model_routing.py`
  (`PipelinePhase.CHAT`, from `models.yaml`) through the chat registry (`config/chat_models.yaml`,
  default `claude-sonnet-5-5`) and `ChatService.provider` (Anthropic only).
- Only one adapter exists: `src/llm/adapters/anthropic_api.py` (plus `fake.py`).
- Admin "Model split" (`platform_defaults.model_split`, migration 0053) is read in
  `app/container.py` and applied only on the router path.

### Files

- The UI's only upload path is `documents` (`src/documents/service.py`, routes in
  `api/routes/documents.py`, `web/components/ws/DocumentDrop.tsx`). Limits in
  `config/research_input_modes.yaml`: 100 MiB per file, 500 MiB and 20 files per run, 2,000 pages,
  5,000 passages, 50 MiB of text, and one sandbox parse per document with 300 s of CPU and
  1,536 MB of address space. The browser itself refuses more than 25 MiB (`web/lib/documents.ts`
  `MAX_FILE_BYTES`) and 20 files (`MAX_FILES`).
- Locally the upload is a single PUT of the whole file to `/v1/documents/{id}/content`, through
  the Next.js BFF (`web/app/v1/[[...path]]/route.ts`), which reads the **entire body into memory**
  (`request.arrayBuffer()`) before forwarding it. In the cloud the start route returns a signed
  single-shot PUT URL (no resume).
- A second, older subsystem, `research_files` (`src/research_files/`, migration 0051, routes
  `/v1/files/...`), has resumable 8 MiB parts (10 GiB limit) but parses **in process** (the
  native abort OI-IM-13, exit 134). Its uploader `web/components/ws/FileUploader.tsx` is no longer
  mounted anywhere since Phase 4 replaced the old Files tab.
- "Choose from storage" is in `DocumentDrop.tsx` (`m.research.fromStorage`, `openStorage`, the
  `GET /v1/documents` list).
- Company detection: `src/documents/company.py` `Resolver` (domains, names, email signals,
  ambiguity margin), `confirm_company` in the service and `/v1/documents/confirm-company`.
- Google Drive and Gmail: `src/connectors_client/drive.py`, `gmail.py`, `maildrive.py`,
  per-user connections (0053, 0059). Without OAuth apps the Mail and drive cards say "Not
  available yet". No Drive **picker** and no Drive-to-storage copy into file analysis.
- Local object storage: `LocalBlobStore` under `PAYLOAD_DIR`; the cloud uses `GcsBlobStore`
  (`src/research_files/blobstore.py`). The replica stack has no storage emulator.

### Cause of the earlier 1 GB failure

No log of the test survives: the containers keep about a day of logs and the API logs only
request lines. The owner's own account (2026-10-14: "my 1 GB file was coming and stopping
because the process has only 20 shares") and the code show these stops, in the order a 1 GB file
meets them:

1. The browser refuses any file over 25 MiB (`MAX_FILE_BYTES`) and more than 20 files
   (`MAX_FILES = 20`; server `max_files_per_run: 20`). This "20" is the limit the owner saw.
2. The server refuses a declared size over 100 MiB and more than 500 MiB per run.
3. The local upload is buffered whole in the Node BFF (`request.arrayBuffer()`): a 1 GB body
   holds 1 GB of memory in the web process and again in the forwarded request.
4. If it got that far, the whole document is parsed in **one** sandbox subprocess with a 300 s
   CPU limit and 1,536 MB `RLIMIT_AS`: a 1 GB spreadsheet or CSV runs out of either and the parse
   is killed; the 50 MiB text cap and 5,000 passage cap truncate the rest.
5. On the older `research_files` path the parse ran in process, where a native abort kills the
   worker (OI-IM-13).

The fix is structural (section 3 below): direct resumable uploads to object storage, a parts
table, many bounded sandbox parses in parallel, streaming readers, and no hidden caps.

### Limits and credits

- `client_settings` and `platform_defaults` (0053): account cap per user (34), idle expiry,
  monthly credit pool, fastest schedule, credit cap per pull, model split.
  `replica/settings.py` `ensure_account_room` enforces the cap under a row lock;
  `runs.lifecycle.add_account` is the one insert path.
- Credits are shown to users on Refresh, New research and Schedules (`replica/credits.py`,
  `replica/jobs.py`).

### Research durability

- Every paid provider call goes through the ledger with an idempotency key; raw responses are
  stored before parsing (`LedgerAdapter`, payload store). Model replies are stored per run and in
  the shared research cache (`RESEARCH_CACHE=on`).
- `runs/sweeper.py` ends orphaned runs `worker_lost` (PARTIAL with data kept, or FAILED and
  refunded). `run_input.resume_from` reuses a previous run's pulls and replies
  (`workflow/deps.py` `resume_from`, `ConnectorProvider(resume_from=...)`); `POST /v1/runs` accepts
  it (`api/routes/runs.py` around line 291). There is no "Resume research" in the UI, no stalled
  state, no per-run dollar cap and no repeated-call detector.

### Downloads

- Deliverables per account and "Download all" as an in-memory zip (`exports/workspace.py`
  builds bytes); tables export the visible filter to XLSX (`api/routes/account_workspace.py`
  sheets). No CSV, no archive job, no range requests, no split past Excel's row limit.

### Isolation

- 203 tables; forced RLS by `tenant_id = app_tenant()` on every tenant table (199 policies).
  `db/session.py` `tenant_txn` sets only `app.tenant_id`; 191 call sites in 49 files.
- Per-user state exists only in places: `chat_threads.owner_user_id`,
  `connector_connections/connector_items.owner_user_id`, `accounts.added_by`,
  `documents.uploaded_by`. Accounts are unique per `(tenant_id, domain)`, so two users of one
  client cannot each have the same company. About 120 tables hold user content (by `account_id`,
  `run_id`, `document_id`, thread, job or upload).

## 2. What changes

### 2.1 Model choice (prompt section 1)

- Catalogue: one file, `config/models.yaml`, gains a `choices` block: Claude Opus 5.5
  (`claude-opus-5-5`, "Most capable, slower"), Claude Fable 5.1 (`claude-fable-5-1`, "Fast and
  strong"), Kimi K3 (`kimi-k3`, "Kimi K3 by Moonshot AI"), each with provider, prices and
  capability flags (server web search, citations, adaptive thinking, prompt caching, strict
  JSON schema). Prices: Opus and Fable from the existing rate table; Kimi K3 from Moonshot's
  price page (input $3.00, cached input $0.30, cache write $3.00 per 5 min TTL, output $15.00 per
  million tokens).
- Two roles: **research** (tiers planner, extractor, classifier) and **analysis** (tiers
  synthesis, narrative, judge, plus Ask and file analysis). Defaults Opus 5.5 and Fable 5.1 (the
  owner's split, which is the `automatic` arm; `cost_saving`'s Sonnet extraction is retired).
  G12 stays true: when both roles name the same model, the judge uses the other Claude model.
- `src/llm/adapters/moonshot_api.py`: OpenAI-compatible Chat Completions on
  `https://api.moonshot.ai/v1/chat/completions`, `Authorization: Bearer $MOONSHOT_API_KEY`,
  `max_completion_tokens`, `reasoning_effort` (low / high / max; no `thinking` field for
  `kimi-k3`), `response_format: {type: json_schema, json_schema: {name, schema, strict: true}}`,
  SSE streaming with `stream_options.include_usage`, usage from `prompt_tokens`,
  `completion_tokens`, `prompt_tokens_details.cached_tokens` / `cache_write_tokens`. Errors mapped
  from the documented types: 429 `rate_limit_reached_error` and `engine_overloaded_error`
  retryable, `exceeded_current_quota_error` not; 500 / 503 / 504 retryable; 400, 401, 403 not.
  Sources: platform.kimi.ai/docs/api/chat, /docs/api/errors, /docs/pricing/chat,
  /docs/pricing/limits (read 2026-10-14). Key from `MOONSHOT_API_KEY` (name only in
  `.env.example`), never logged, never sent to the browser or into a prompt.
- Kimi has no server web search, citation blocks or adaptive thinking here: as the research
  model it reaches the web only through the platform's data tools and the ledger. A step that
  needs a strict schema and gets an invalid reply from Kimi falls back to the Claude default for
  that step, and the call record says why (`fallback_reason`).
- Per-run and per-answer record: `account_runs.models` (research, analysis, fallbacks) and
  `chat_turns.model_id` plus the role; shown in run details and "What this answer used".
- User preferences: `user_preferences` (tenant, user, research_model, analysis_model), owned by
  the user. Ask's model chip sets the analysis model for the conversation
  (`chat_threads.analysis_model`); New research and Refresh carry both chips.
- Admin: `platform_defaults.enabled_models`, `default_research_model`, `default_analysis_model`
  (replace `model_split`); `client_settings.allowed_models` (Kimi K3 off until enabled for that
  client). `GET /v1/models/choices` returns each model with `available` and a reason ("Not set
  up", "Not enabled for your workspace"); the API refuses an unavailable choice before any work
  starts.
- `config/model_routing.yaml`: remove the Haiku candidate; candidates become the three choices.
  The router stays off (D-51 unchanged); the user's choice applies on the legacy path, which is
  the live one.

### 2.2 Two ways to work (prompt section 2)

- Remove "Choose from storage" (DocumentDrop, messages, the list call there).
- File sources: **Add files** (device) and **Connect Google Drive** (picker; the server copies
  the chosen files to storage in chunks). Until the owner's Google OAuth app exists, "Connect
  Google Drive" shows "Not available yet". The composer "+" opens the same two choices.
- Company detection decides the account: one company attaches the files (creating the user's
  account if needed); several ask which one; none asks the user to name it. Findings stay
  Internal, cite file and location, never enter shareable files (two-output rule, leak check).

### 2.3 Files at any size (prompt section 3)

- **Direct resumable uploads.** `POST /v1/uploads` creates an upload session in object storage
  and returns its URL; the browser sends 32 MiB chunks straight to storage with
  `Content-Range` and asks storage for the committed offset after a reload or drop (the GCS
  resumable-upload protocol). Cloud: GCS resumable sessions (`create_resumable_upload_session`
  with the web origin for CORS). Local: a small development object store service in the replica
  stack (`src/devstore/`, compose service `objectstore`) that implements the same subset of the
  protocol on a directory, so the browser code and the tests are identical and the file never
  passes through Next.js or the API. Progress is real bytes acknowledged by storage.
- Maximum file size: admin setting `platform_defaults.max_file_bytes`, default 20 GiB. No other
  file count or run size cap remains except deliberate, configurable ones.
- **Parts.** After the scan, a splitter streams the stored file once and writes
  `document_parts` rows: byte ranges on line boundaries for CSV, TSV, TXT, MD and JSON lines; page
  ranges for PDF; row ranges per sheet for XLSX; one part for DOCX and PPTX (bounded by size);
  time ranges for audio only if a transcription vendor is configured. Each part has status
  (pending, running, done, failed with reason), attempts and a lease.
- **Workers.** One task per part through the existing scheduler (`/tasks/...`), with a
  configurable concurrency limit per tenant and in total; each part parses in the sandbox
  subprocess with its own memory and time limits, reading with streaming readers. Passages carry
  `(document_id, part_seq, seq)` with a unique key, so a part that runs twice inserts nothing
  new. A failed part says why and has "Try again" for that part alone.
- Progress: parts done of parts total, on the file and on the account.
- Storage: raw files under each tenant's prefix; passages and parts indexed tenant and owner
  first; passages partitioned by hash of tenant; every list paged.
- Tests: `large_file` marker (not in the default run): a generated multi-GiB CSV streamed
  through upload and processing with peak memory under a ceiling; kill a worker mid-file and
  resume with no duplicate passages.

### 2.4 Research that never loses data and never pays twice (prompt section 4)

- Verify ledger idempotency and store-before-use on every path, including Kimi model calls
  (model replies are stored per run before parsing, as for Claude).
- Stop for any reason keeps everything, marked PARTIAL with the reason (already true for stop,
  sweep and cap; extend to provider outage and the dollar cap).
- **Resume research**: `POST /v1/accounts/{domain}/resume` starts a run with
  `resume_from = latest partial run`; reused calls come from the ledger and the research cache
  and are not paid again; the screen shows "Resuming: N of M steps already done".
- Freshness reuse within the window (already the research cache; verify a re-run pays only for
  what is missing or stale).
- Progress per step with real counts from work units and ledger rows. A heartbeat column
  (`account_runs.progress_at`); the sweeper marks a run with no progress for
  `stall_after_s` as **stalled**, notifies the owner and offers Resume.
- Hard dollar cap per run (`platform_defaults.run_dollar_cap`, default $25): checked before each
  paid call against the ledger's real cost; reaching it stops the run PARTIAL.
- Repeated-call detector: the same provider and normalised query paid more than
  `repeat_call_limit` times in one run stops the run and writes a `spend_defects` row for the
  owner.

### 2.5 Limits per user (prompt section 5)

- `client_settings`: `research_limit_default` (2), `chat_limit_default` (40), `limit_reset`
  (`monthly` or `never`); same defaults on `platform_defaults`.
- `user_limits` (per user overrides) and `usage_periods` (researches and chats used per user per
  period), both owned by the user; a research is counted when it starts (new research by
  company, Refresh from web, scheduled refresh), a chat when a question is asked or a file
  analysis starts. A started research always finishes; the dollar cap still applies.
- Refusal: "You have used your 2 researches. Ask your admin to increase your limit." (or "your
  40 chats"); "Request more from admin" creates a `limit_requests` row (user, client, limit,
  note); the operator console lists it; Approve sets new limits, Decline closes it; the user is
  notified in the app; email only if a mail channel is configured. Every change is audit-logged.
- Users see "1 of 2 researches left this month" on New research and "12 of 40 chats left" by the
  composer. The account cap, the accounts meter and every credit figure leave the user's screens;
  the credit ledger stays for Runs and spend.

### 2.6 Everything collected is downloadable (prompt section 6)

- "Download everything" per account: a background job writes one zip as a stream to storage in
  parts (every deliverable, every table in full as CSV and XLSX, facts with grade, citation and
  the exact sentence, the coverage tracker, the user's own file findings and files), then a
  short-lived signed link. Tables past 1,048,576 rows split across sheets; CSV is always
  complete. Downloads support HTTP range requests.
- Every table: "Open in Excel" and "Download CSV" for the whole current filter.
- Client-safety rules apply to anything shareable; raw provider responses are left out
  (owner decision, default).

### 2.7 Every login is its own workspace (prompt section 7)

- `app.user_id` next to `app.tenant_id`: `tenant_txn(tenant, user=...)` and a context variable
  set from the verified session at the start of each request and from the run's owner at the
  start of each worker task. System jobs (scheduler tick, sweeper, isolation check, nightly
  jobs, operator counts) open `tenant_txn(..., system=True)`, which sets `app.scope = system`.
  With neither, owned rows are invisible (fail closed).
- `owner_user_id` on every user-content table (about 120; listed in `config/ownership.yaml`, with
  how each is backfilled), default from `app.user_id`; RLS `tenant_id = app_tenant() AND
  (owner_user_id = app_user() OR app_scope() = 'system')` for USING and WITH CHECK. Accounts
  become unique per `(tenant_id, owner_user_id, domain)`.
- Backfill: from `accounts.added_by` through account, run, document or job; where the creator is
  unknown, the client's first active user; every choice is written to `ownership_backfill`.
- Tenant-level tables (client settings, memberships, users, audit, auth, library, platform) stay
  tenant-only, listed in `ownership.yaml` with the reason. A coverage test fails on any tenant
  table in neither list.
- A new user lands in an empty workspace with "Add your first account" and "Add files"; the demo
  seed stays development only.
- 6th admins: counts and categories only; View as a client still needs the grant.
- Tests: cross-tenant and cross-user probes for every owned table and route; the nightly
  isolation check probes the owner rule too.
- Seam for team sharing later: `owner_visible()` is one SQL function, so a future share table
  changes one place.

### 2.8 Minimal UI (prompt section 8)

- Keep the design system; one primary action per screen; real data or an honest empty state;
  skeletons, empty and error states; no credits, account cap or sample text.
- Screens to show the owner first (1360 and 375): Ask with the model chip, New research (both
  tabs), file processing with progress, limit reached, the request flow, the admin Requests list,
  Download everything.

## 3. Migrations

| # | What |
| --- | --- |
| 0063 | `app_user()`, `app_scope()`, `owner_visible()`; `owner_user_id` on owned tables with defaults, backfill, indexes, new policies; accounts unique per owner; `ownership_backfill` |
| 0064 | `user_preferences`, model columns on `platform_defaults`, `client_settings`, `account_runs`, `chat_threads`, `chat_turns`; drop `model_split` |
| 0065 | `user_limits`, `usage_periods`, `limit_requests`; limit defaults on both settings tables |
| 0066 | `uploads` (resumable sessions), `document_parts`; passage key `(document_id, part_seq, seq)`; `max_file_bytes` |
| 0067 | `account_runs.progress_at`, `stalled_at`; `run_dollar_cap`, `repeat_call_limit`, `stall_after_s` |
| 0068 | `download_jobs` (archive builds) |

Every migration has a down file, passes the migration lint and the RLS catalog check, and adds
its tables to the isolation matrix.

## 4. Risks

- **Ownership touches everything.** A missed code path that opens `tenant_txn` without a user or
  `system=True` sees no owned rows. That is the safe direction (an empty page, never a leak), and
  the full suite plus the click-through will find each one. Done first, so later work is built on
  it.
- **Accounts per owner.** Changing the unique key changes how `/v1/accounts/{domain}` resolves;
  under the owner policy a domain is unique for the caller, so routes keep their shape.
- **Kimi K3 is unverified live.** No key is set; the adapter is built from the documentation and
  tested against a recorded-shape fake. The first live call needs the owner's key and "go".
- **Drive and Gmail** need the owner's Google OAuth apps; built to the point of the missing
  input with a fake provider in tests.
- **Large-file tests are slow**; they run under the `large_file` marker, not in every run.
- **Dev object store** is development only; the container refuses it outside dev and test.

## 5. Order

1. FIX_PROMPT Phase 4: record it in FIX_LOG with the section 9 checks; commit.
2. This plan; reversals in PLAN.md and REPLICA_PROMPT.md section 0.
3. Per-user isolation (0063) and its tests.
4. Per-user limits and requests (0065), replacing the account cap and credits on user screens.
5. Model choice and Kimi K3 (0064).
6. Resumable uploads, the dev object store, parts and parallel parsing (0066), large-file tests.
7. File sources: remove "Choose from storage", Drive picker and copy, company attach flow.
8. Resume, stalled, dollar cap, repeated-call detector (0067).
9. Download everything and per-table CSV (0068).
10. UI pass on the listed screens; screenshots to the owner.
11. Section 9 checks, click-through on fakes, FIX_LOG report.
