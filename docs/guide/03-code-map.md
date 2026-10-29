# 3. Code map: which code is used for which thing

*Part of the [project guide](README.md). Written 2026-10-29. "Real" means the code does work in
the product. "Stub" means the functions exist but do nothing (they are there so requirement tests
can import them).*

All Python lives in `src/`. The big package is `src/account_intelligence/`; the other 36 are
small top-level packages. Packages import in **one direction only** (checked by `make static`
using the contracts in `pyproject.toml`), so lower layers never import higher ones.

## 3.0 Find the code by question

| If you want to know… | Look at |
|---|---|
| what happens when someone presses Start | `api/routes/research.py` → `replica/jobs.py` → `runs/lifecycle.py` → `api/routes/tasks.py` → `workflow/runner.py` |
| why a run stopped | `workflow/runner.py` (`_timed_out`, `_end_no_progress`, `_stop_unexpected`), `runs/sweeper.py`, `replica/auto_resume.py` |
| how a fact gets its grade | `account_intelligence/grading/` (`pipeline.py`, `product.py`, `primary.py`, `engine.py`) |
| what the research steps are | `agents/graph.py` |
| how a paid call is made and not paid twice | `account_intelligence/connectors/service.py`, `ledger.py`, `cache.py` |
| how files are built | `exports/builder.py`, `exports/readers/`, `exports/word/`, `exports/excel/` |
| what a screen calls | `web/app/...`, `docs/WIRING.md`, `web/lib/gen/api.gen.ts` |
| how one customer is kept from seeing another's data | `account_intelligence/db/session.py` (`tenant_txn`), `migrations/` (row-level security) |
| which setting controls X | [04-config-map.md](04-config-map.md) |

## 3.1 The big package: `src/account_intelligence/`

| Folder | Purpose in plain words | Key files |
|---|---|---|
| **`app/`** | The one place where every outside thing is plugged in ("wiring"). In dev/test it plugs in fakes; in staging/production only the real Google Cloud adapters (it refuses to start otherwise). | `container.py` (`build_container`, `deps_for`) |
| **`config.py`**, **`config_files.py`** | `config.py`: every environment variable, validated at start-up (names only, never values). `config_files.py`: loads and hashes the YAML rules. | |
| **`api/`** | The web server: `/v1/*` for the website, `/operator/v1/*` for staff, `/tasks/*` for the worker. Every route needs an entry in a catalogue or it answers 403 (default deny). | `main.py` (start-up, sweeper), `guard.py` + `policy.py` (route catalogue), `auth.py`, `contract.py` (all request/response shapes), `routes/*.py` (~35 modules; `runs.py`, `research.py`, `tasks.py`, `ask.py`, `chat.py`, `documents.py`, …) |
| **`db/`** | Database plumbing. `tenant_txn(tenant)` is the *only* way to run tenant queries, so row-level security always applies. | `session.py`, `owners.py`, `source_ids.py`, `models/` |
| **`platform/`** | "Ports" (interfaces) for outside things plus the real and fake adapters. | `ports.py`, `gcp/adapters.py` (Cloud Tasks, Storage, Secret Manager, token check), `llm.py` (Claude calls, batching), `prompt_cache.py`, `checkpoint.py`, `hermetic.py` / `fake_model.py` (dev only) |
| **`runs/`** | The state machine and the money. The only code that creates, moves and ends runs. | `lifecycle.py` (`create_run`, `begin_slice`, `finish`, `count_no_progress`, `model_replies`), `transitions.py`, `sweeper.py`, `stop.py` |
| **`workflow/`** | The slice engine and the older 13-stage Workflow v1. | `runner.py` (`execute_slice`), `engine.py`, `deps.py` (`RunDeps`), `handlers.py`, `stages/`, `spec.py` |
| **`connectors/`** | The doorway to every paid or free **data provider**. Pipeline: validate → budget → lease → circuit breaker → rate limit → call → store → normalise → ledger. Providers are only ever named by code (`p_search`, `p_scrape`, `p_signals`, `p_people`, `p_org`). | `service.py`, `ledger.py`, `cache.py`, `spend_guard.py`, `ratelimit.py`, `breaker.py`, `http.py`, `security.py` (blocks internal addresses), `settings.py` (`uncapped_config`), `providers/p_*.py` |
| **`grading/`** | Turns a claim into a graded fact (rules in 2.6). Pure maths plus the only write path for facts. | `pipeline.py`, `spans.py`, `engine.py`, `primary.py`, `product.py`, `judge.py`, `leads.py`, `repo.py`, `config.py` |
| **`tracker/`** | The 33-row coverage checklist and the "may this be called Completed?" verdict. | `service.py`, `derive.py`, `completion.py`, `guard.py`, `gap.py` |
| **`replica/`** | Everything behind the customer screens: jobs and credits, the live research view, Ask answers, tables and downloads, schedules, the shared Library, admin read models. | `jobs.py`, `credits.py`, `auto_resume.py`, `live.py`, `answers.py`, `model_answer.py`, `tools.py`, `sheets.py`, `downloads.py`, `schedules.py`, `admin.py`, `isolation.py` |
| **`worker/`** | Command-line entry points (Cloud Run Jobs): one run to its stop, nightly spend checks, re-indexing. | `__main__.py`, `job.py`, `nightly.py` |
| **`chat/`** | Private chat threads with a model, streaming, cost tracking, per-tenant keys. | `service.py`, `store.py`, `registry.py`, `grounding.py` |
| **`rag/`** | A retrieval toolkit (search + answer with citations, safety checks). Whether any production route uses its graph is unverified. | `retrieval.py`, `graph.py` |
| **`safety/`** | The client-output scanner: nothing named in the deny-list may reach a client file. | `client_output.py` |
| **small ones** | `model_routing.py` (phase → model), `urls.py` (canonical URLs; changing a rule splits sources, treat as a data migration), `dev_limits.py` (the "caps off" switches) | |

## 3.2 The research brain and its helpers (top-level packages)

| Package | Purpose | Key files |
|---|---|---|
| **`agents/`** | The research graph and its rules (**guards G1–G17**). | `graph.py` (all nodes), `guards.py`, `extraction.py` (the reading step), `entity.py` (wrong-company lock), `pull/agent.py` (code-only fetcher), `sense_check/`, `distill/`, `modes.py`, `manifest.py`, `skill_context.py` (injects the owner's skill into model calls), `naming.py` (G9: no tool or person names in client text), `production/` (real implementations: `wiring.py`, `provider.py`, `grading.py`, `pipelines.py`, `delivery.py`) |
| **`jobs/`** | Finds the job postings of the last 12 months, finds which platforms each mentions, counts them. Counts come from rows, never typed numbers. A platform is "named" only if it is in the description (a title-only hit is a Lead). | `pipeline.py`, `stage.py`, `classify.py`, `counts.py` |
| **`people/`** | Builds the list of senior people who own or lead each platform area, with level, priority and tenure. Only runs with the customer's people-data consent. | `pipeline.py`, `scope.py`, `merge.py`, `counts.py`, `erasure.py` |
| **`llm/`** | The only door to a model: picks the model per task, sends, validates, records cost, replays stored replies. | `port.py` (Router), `routing.py`, `reply_cache.py`, `record.py`, `adapters/` (Claude, Kimi, Grok, fake) |
| **`exports/`** | Builds the client files from one consistent snapshot: 2 Word documents, 3 Excel workbooks, 1 zip. Leak-scanned and verified before anything is stored. | `builder.py` (`build_all`), `readers/` (rows → render model), `word/`, `excel/`, `verify.py`, `store.py`, `workspace.py` |
| **`documents/`** | The customer's own uploaded files: safe intake (type check, malware scan, locked-down parser), redaction, indexing for Ask, the **file-mode research run**, the **quality gate**, the Internal Only Word file. | `service.py`, `scan.py`, `sandbox.py`, `chunking.py`, `tables.py`, `retrieve.py`, `graph.py`, `gate.py`, `internal_doc.py` |
| **`research_files/`** | Low-level file handling: detect type from the first bytes, stream-store, parse into resumable units, audit checks. | `detect.py`, `blobstore.py`, `parsers/`, `checks.py` |
| **`views/`** | On-screen tables for finished research, built from the *same* mapping the exports use, so screen and file cannot disagree. | `rowsources/`, `materialize.py`, `query.py`, `parity.py` |
| **`parallel/`**, **`verify/`** | Used only by the older Workflow v1: a concurrency scheduler; a blind second check that can only lower a grade. | |
| **`crosscut/`** | `fanout.py`: run independent calls side by side (limit 8, a started paid call is never cancelled for a sibling's failure). The ABM graph uses this, not `parallel/`. | `fanout.py`, `conformance.py` |

## 3.3 Money, limits, security, observability

| Package | Purpose | Real? |
|---|---|---|
| **`spend/`** | The credit cap for paid research: propose, approve, block, pause for approval, report. | real |
| **`metering/`** | Counts usage and enforces plan limits with one atomic update. | real |
| **`governance/`** | Monthly allowance for background monitoring, with an 80% warning. | real |
| **`tenancy/`** | Per-customer settings, storage paths, tenant lifecycle. | real |
| **`identity/`** | Sign-in (company SSO or local password), sessions, CSRF, roles, rate limits. | real |
| **`admin/`** | The staff (operator) plane: tenants, quotas, feature flags, time-boxed support access. | real |
| **`audit/`** | A tamper-evident diary: each row carries the previous row's hash. | real |
| **`privacy/`** | Data-subject requests, retention, a "do not re-add" list. No importer found in `src/`; runtime use unverified. | real code, unverified use |
| **`obs/`** | Logging, metrics, tracing, health. `stalldump.py` writes every thread's stack if the loop stalls 15 s (added 2026-10-28). | real |
| **`security/`**, **`metrics/`**, **`registers/`**, **`unitcost/`** | **Stubs.** `security` always reports "all clear" (it scans nothing). Do not describe them as working controls. | stub |

## 3.4 Connections and extras

| Package | Purpose |
|---|---|
| **`connectors_client/`** | The customer's *own* mail, drive and CRM (read-only, narrow consent). Not the research providers. |
| **`crm_writeback/`** | Optional create-only push of findings into a CRM. Dormant until a CRM app is registered. |
| **`signals/`**, **`refresh/`**, **`alerts/`** | Scheduled re-checks: turn "something changed" into one scored event, decide cheap probe vs full refresh, send alerts and a weekly digest from fixed templates (no model writes a word). |
| **`opendoor/`**, **`pilot/`** | A published event format for a future outreach tool; pilot feedback metrics. |
| **`evalgate/`**, **`quality/`**, **`contracts/`** | Quality gates: golden-account evaluation that blocks regressions; release rules; detection of provider response-format drift. |
| **`datamodel/`** | Small honesty rules: entity lock writer, careers URL only if visited, counts shown with their base. |
| **`devstore/`** | A fake cloud file store for local work (refuses to run outside dev/test). |

## 3.5 Outside `src/`

| Folder | What it is |
|---|---|
| **`web/`** | The Next.js website. Browser → web only; `/v1/*` is proxied to the API server-side with the session cookie. Screens: Ask (home), Accounts and the account workspace (with deep-dive tables), Research board and New research, Schedules, Mail and drive, Team, Support access, Model preferences, and a staff-only Operator console. Many old screens now just redirect to `/`. |
| **`migrations/`** | numbered SQL steps 0001 to 0085 (0069 is skipped). Every tenant table has row-level security forced on. After adding one, bump `EXPECTED_REVISION` in `api/routes/health.py`. |
| **`infra/`** | Terraform: Cloud Run services and jobs, Cloud SQL, Redis, Storage, Cloud Tasks queues, Scheduler ticks, secrets, load balancer + Cloud Armor, monitoring. |
| **`docker/`** | The Dockerfiles (`api`, `worker`, plus `web/Dockerfile`). |
| **`tests/`** | Synthetic-data tests organised by area. Markers `eval` and `live` call the paid model: always run with `-m "not eval and not live"`. |
| **`scripts/`** | Gates (comment, route-catalogue, telemetry checks) and generators whose output is committed and drift-checked (OpenAPI, web client, WIRING.md, module map, loops register). |
| **`ops/`, `evals/`, `runtime/`** | Local ops tools; golden data for evaluations; Word/PowerPoint render tools installed in the images. |
| **`unwanted-things/`** | A parking lot. Rule: move, never delete. Nothing imports it. |

## 3.6 Rules the code lives by (so you can predict it)

* **The database enforces who sees what** (row-level security), never just application code.
* **The AI never chooses a grade or a count.** Code does. The AI proposes quotes; code checks them
  word for word.
* **A paid call is never made twice.** It is recorded before it is made and replayed afterwards.
* **Gaps are shown, not hidden.** A row that could not be filled is a named gap with a reason.
* **Provider and tool names never reach a client.** They appear only by code (`p_search`…) in
  internal files, and client files say "6th agents".
* **One retry owner.** The graph node retry policy retries provider errors; the SDK and connectors
  do not.
* **Every loop needs a stated cap and exit** (`docs/architecture/loops.md`, checked by
  `scripts/gen_loops.py --check`).

Next: [4. Config map](04-config-map.md).
