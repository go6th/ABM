# 0. Structure map (the whole project on one page)

*Part of the [project guide](README.md). Written 2026-11-03 from the code on disk, including that
day's uncommitted changes (ADR-0106 to ADR-0128, the one model router, the retired stubs). The
graph steps, slice settings, scheduler ticks and router files were checked in the code; the rest
comes from reading the source. Pages 1 to 6 are older (2026-10-29); where they disagree, this page
is newer.*

## 1. What it is

**Account Intelligence** (the "ABM Generator") is a multi-tenant web app for sales teams. A user
names a company; the platform researches it on the web and in the user's own files, grades every
fact, and returns a Word brief, Excel sheets (people, jobs, tech, news) and a zip. It adds **Ask**
(a chat over everything found) and scheduled refreshes. Each customer (tenant) is sealed off by
PostgreSQL row-level security.

## 2. What runs

Three services on Google Cloud Run (project `abmgenerator`) plus a migration job:

| Service | Code | Job |
|---|---|---|
| **web** | `web/` (Next.js 16, React 19) | The screens. The browser only talks to this; `/v1/*` is passed to the API. |
| **api** | `src/` with `SERVICE_ROLE=api` | Sign-in, permissions, reading and writing data, starting work. |
| **worker** | the same `src/` with `SERVICE_ROLE=worker` | Slow work: research runs, reading uploads, scheduled jobs. Cloud Tasks and Cloud Scheduler post to `/tasks/*`. |

Behind them: PostgreSQL with pgvector, Cloud Storage, Redis, Secret Manager, the AI models
(Claude; optional Kimi K3 and Grok), and the data providers.

The api and worker are **one program**: `SERVICE_ROLE` decides which routes it serves
(`src/account_intelligence/api/planes.py`); a route from the wrong role returns 404, and `all` is
refused in staging and production.

## 3. Folders by size

| Folder | Size | What's in it |
|---|---|---|
| `src/` | ~200k lines of Python | All backend code (section 4) |
| `tests/` | ~122k lines | About 40 folders, one per subsystem |
| `web/` | ~21k lines of TypeScript | Customer screens and the staff console |
| `config/` | 125 files | Rules read as data: models, grading, prices, providers, the skill text |
| `migrations/` | 0001 to 0101 | Each change is a SQL file, a `.down.sql` and an Alembic wrapper in `versions/` |
| `infra/` | Terraform | Modules, `stack/main.tf`, the staging and prod roots |
| `docker/` | Dockerfiles and CI scripts | api and worker images; the worker has a cpu/gpu variant |
| `scripts/`, `ops/`, `evals/`, `render/` | Tooling | Gates, generators, the fake sign-in server, golden data, the TypeScript Word renderer |
| `docs/` | 583 files | ADRs up to 0128, this guide, the audit PDF, runbooks |
| `unwanted-things/` | Parked files | Moved here, never deleted (`README.md` lists each one) |
| `.claude/` | 3.6 GB | Almost all of it is old git worktrees in `.claude/worktrees/`, not project code |

## 4. Inside `src/`, in layers

**The application package, `src/account_intelligence/`** (about 67k lines):

| Subpackage | What it does |
|---|---|
| `api/` (20k lines) | About 236 routes in 37 modules. `policy.py` lists every route and anything unlisted is refused; `guard.py` checks role, membership, CSRF, the MFA step-up and the read audit; `contract.py` holds 302 response models that generate `docs/api/openapi.json` and `web/lib/gen/`. |
| `replica/` (14.6k lines) | The live product's server side: account workspace, deep-dive sheets, credits, research jobs, schedules, Library, Download everything, Ask. The name comes from "working replica of the design prototype"; it is not a copy. |
| `app/container.py` | The one place that picks fake adapters (dev, tests) or Google Cloud adapters (staging, prod). |
| `db/` | `tenant_txn()` is the only way into tenant data; it sets tenant, user and scope for RLS. |
| `connectors/` | Every paid provider call goes through `service.call_operation`: budget, lease, ledger (never pay twice), breaker, rate limit, cost record. |
| `workflow/` | `runner.py` cuts a run into 1650-second slices (up to 16; three slices with no progress stop the run), with checkpoints and resume. |
| `grading/`, `tracker/` | Fact grading and the 33-row research checklist. |
| `chat/`, `rag/`, `safety/`, `runs/`, `worker/`, `archive*.py` | Chat threads, retrieval, the client-output scanner, run lifecycle and settlement, the worker CLI, the link archive. |

**The research graph, `src/agents/`:** a LangGraph flow (`graph.py::build_abm_graph`) with 18
steps:

| Stage | Steps |
|---|---|
| Setup and checks | `setup`, `entity_lock` (right company?), `cap_approval` |
| Fetching | `pull` (every checklist row, no model), `spend_gate` |
| Reading | `load_manifest`, `interpret` (pass1, pass2 and people extraction run inside it), `pipelines` (jobs, tech, people), `person_searches` |
| Checking and summarising | `sense_check` and `targeted_pulls` (follow-up loop), `sense_approval`, `distill`, `token_gate` |
| Output | `build` (the files), `gate` (withholds a bad file instead of failing the run), `deliver`, `finish` |

Each model step's prompt is `src/agents/<step>/prompt.md`; the owner's skill text from
`config/skill/archive/` is prepended by `agents/skill_context.py`.

**Files and pipelines:** `exports/` (23k lines: Word, Excel, the zip, `repair/`), `documents/` +
`research_files/` + `devstore/` (uploaded files), `jobs/` and `people/` (pipelines), `parity/`
(output parity with the BD reference).

**Money and tenants:** `spend/`, `metering/`, `identity/` (sign-in, SSO, sessions), `tenancy/`,
`admin/` (staff console), `audit/` (hash-chained log).

**Models:** `src/llm/`. One router, `llm/route.py`: task kind → tier → `config/models.yaml` task
(ADR-0113). `config/model_routing.yaml` and about 30 old modules were deleted on 2026-11-03.

## 5. One research run, start to finish

```
Web "Research a company" → POST /v1/research (api/routes/research.py)
  → research_jobs row + credit reservation → Cloud Tasks → /tasks/research-job
  → GraphEngine: create_run (account_runs + runs + tracker seeded) → queue slice 0
  → /tasks/run-slice → workflow/runner.execute_slice
       lease lock → LangGraph Postgres checkpoint → agents/graph.py (18 steps)
       slice times out → if progress, queue next slice; 3 slices with none → stop
  → build → gate → files stored as deliverables/<sha>.<ext>
  → lifecycle.finish → settle credits (used ≤ reserved, rest released) → spend report
Every 5 min: /tasks/sweep ends dead runs, marks stalled ones, auto-resumes eligible ones
```

## 6. The other main flows

- **Ask** (`POST /v1/chat/ask`, `api/routes/ask.py`):
  1. Load the workspace.
  2. Route the question as table, prose or mixed (`replica/router.py`).
  3. Answer with tools (`replica/tools.py`): table queries, `count_tiers`, passage search,
     archived pages, and web search (on by default since 2026-11-02, `ask_web` in `config.py`).
  4. Check the citations, clean the text, stream the answer back.

  Models offered (`config/chat_models.yaml`): Sonnet, Haiku and Opus 5.5, Fable 5.1, Kimi K3,
  Grok 4.5.
- **Upload** (`src/documents/service.py`): the file goes straight to storage, then quarantine and
  a virus scan (clamd), then parsing in a sandboxed subprocess (ADR-0124), then redaction,
  chunking, embedding and indexing. An uploaded People, Jobs or Tech workbook also becomes the
  account's rows (`documents/deliverables.py`).
- **Staff console** (`/operator/*`): staff sign in with Google, then get an `ops_` session
  (30 minutes idle, 8 hours max; ADR-0111). Every action needs a support grant and is audited.

## 7. Scheduled ticks (`infra/stack/main.tf`, routes in `api/routes/tasks.py`)

| Tick | Every |
|---|---|
| `schedules-tick` | 15 min (at most 10 starts per tick, 3 per tenant; ADR-0108) |
| `sweep`, `notify-relay` | 5 min |
| `metrics` | 2 min |
| `maildrive-sync` | hourly at :05 |
| `isolation-check`, `access-expiry`, `audit-verify` | nightly |
| `retention`, `alert-digest` | defined but paused; neither route exists yet |
| `spend-invariants` | nightly Cloud Run job, not an HTTP tick |

## 8. Where the docs and the code disagree, and dead code

- **Two research engines are still live.** The 18-step graph runs everything started from the
  UI; the older Workflow v1 (`workflow/engine.py`) still runs plain `POST /v1/runs` calls when the
  tenant's `abm_agents` flag is off.
- **Unused packages:**
  - Not reached by the running app: `signals`, `privacy`, `quality`, `evalgate`, `contracts`.
  - Mostly unreached: `refresh`, `alerts` (engine, digest, outbox) and most of `rag/` (11 of 15
    files).
  - `src/account_intelligence/harness/` is an empty folder.
- **Credits aren't checked when a research starts** (`enforce_pool=False` in
  `api/routes/research.py`, owner decision 2026-10-14), though that module's docstring says they
  are.
- The graph's `finish` step settles nothing; `lifecycle.finish` in the runner does the real
  settlement.
- **Stale docs:** the runner docstring says 8 slices (the setting is 16); `docs/routing/*` and
  [page 4](04-config-map.md) still describe the deleted `model_routing.yaml`; the Ask docstring
  says it never uses the web.
- ADR-0129 to 0131 are cited in Terraform and `connectors/ledger.py`, but those ADR files don't
  exist.

## 9. Rules that surprise newcomers

- Vendor names may appear only in `config/providers.yaml`; client files say "6th agents".
- The one-writer-per-table rule is enforced by `tests/system/test_single_write_paths.py`, not by
  `config/ownership.yaml`.
- `.env.example` must list exactly the variables the code reads.
- Run tests with `-m "not eval and not live"`: an explicit `-m "not eval"` on its own still
  selects the paid live tests.
