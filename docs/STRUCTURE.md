# Structure: what is live, what is legacy, what can be parked

Written 2026-10-27 for a fresh start. `PROJECT_STRUCTURE.md` at the root is the long map of
every folder (2026-10-16); this is the short version with one question answered per line:
is it on the path a research takes today?

## The live product in one page

| Piece | Path | Live? |
| --- | --- | --- |
| API | `src/account_intelligence/api/` (`main.py`, `routes/`, `policy.py` route catalog, `contract.py` the OpenAPI source) | yes, service `api` |
| Worker | `src/account_intelligence/worker/` (`/tasks/*` routes in `api/routes/tasks.py`, slices in `workflow/runner.py`) | yes, service `worker` |
| Web app | `web/` (Next.js; `app/(client)/accounts/[domain]` is the account workspace, `[sheet]` the deep-dive tables; `lib/gen/api.gen.ts` generated from `docs/api/openapi.json`) | yes, service `web` |
| The research graph | `src/agents/graph.py` (`setup → entity_lock → cap_approval → pull → load_manifest → interpret → pipelines → person_searches → sense_check (→ targeted_pulls) → distill → build → gate → deliver → finish`), wired in `src/agents/production/wiring.py`, run by `workflow/runner.py` slices | yes: the only graph a run executes |
| Phase 1 pulls | `src/agents/pull/agent.py` through `src/agents/production/provider.py` and the connector ledger `src/account_intelligence/connectors/` | yes |
| Reading step | `src/agents/extraction.py` with the prompts in `src/agents/pass1/`, `pass2/`, `people/` (`people/prompt.md` is loaded by the registry but the people step's rows are all pipeline rows, so the call is never made) | yes (people prompt: registered, unused) |
| Code pipelines | `src/jobs/` (postings), `src/people/` (people list), detections in `src/agents/production/pipelines.py` | yes |
| Grading | `src/account_intelligence/grading/` | yes |
| Files | `src/exports/` (Word through `exports/word/render_cli.py`, Excel through `exports/excel/`), workbook tabs read back in `replica/file_tabs.py` | yes |
| Link archive | `src/account_intelligence/archive.py`, migration 0086 | yes (new) |
| Output parity | `src/parity/` (bundle, rules, tables, workbook renderers, gate, `from_run.py`, `service.py`), `render/docx/` (Word renderers), `api/routes/parity.py`, `web/lib/parity/`, `scripts/parity/` | yes when `PARITY_BUNDLE=on`: built beside the export build in the graph's build node, served on the account workspace (2026-10-27) |
| Product screens' server side | `src/account_intelligence/replica/` (the name is historical; this is the product) | yes |
| Chat (Ask) | `src/account_intelligence/chat/`, `replica/answers.py`, `replica/model_answer.py` | yes |
| Uploaded files | `src/documents/`, `src/research_files/` | yes |
| Money and tenants | `src/spend/`, `src/metering/`, `src/tenancy/`, `src/identity/`, `src/admin/`, `src/audit/`, `src/opendoor/`, `src/pilot/` | yes |
| Tables for the screens | `src/views/` (materialised client tables) | yes |

### Configuration that the live path reads

`config/workflow_v1.yaml` (stages, slice length 1650 s, no-progress cut-off), `config/parallel.yaml`
(provider ceilings, used by the governor), `config/models.yaml` (fast or economy batch mode),
`config/providers.yaml` (rate limits, the people attributes), `config/skill/*.yaml` (the pull
plan, attributes, scope, provider routes), `config/export_rules.yaml` and `export_theme.yaml`
(the workbooks and documents), `config/people_rubric.yaml`, `people_scope.yaml`,
`people_sources.yaml`, `config/grading*.yaml`, `config/ownership.yaml` (private workspaces),
`config/plans.yaml`, `roles.yaml`, `routing.yaml`; `config/skill/archive/` holds the owner's skill
text and scripts the system context and the parity gate are built from. `config/parity/` (new)
holds the platform dictionary.

### Legacy on the path, kept because the live path still imports it

* `src/account_intelligence/workflow/engine.py`, `parallel_phase.py`, `stages/*`: Workflow v1's
  sequential engine and the parallel scheduler. The graph does not use the scheduler
  (`execution_mode: sequential`); `runner.py` (slices, leases, checkpoints) and `stages/people.py`
  (`_is_hostile`) are still imported. Candidates to fold into `agents/` later, not to park now.
* `src/account_intelligence/agents/` (inside the application package): the grading facade and
  prompts of Workflow v1, imported by `workflow/stages/grading_stages.py` and the production
  wiring (`LlmJudge`).
* `src/account_intelligence/model_routing.py`: nothing imports it (0 references outside itself).

## Dead or duplicate paths: the list to review before anything is moved

Method: static import reachability from the live entry points (`api.main`, `worker.main`,
`app.container`, `agents.production.wiring`), then a search for references in `tests/`,
`scripts/`, the Makefile, CI, compose files and `docs/*.md`. A package reached by no entry point
and referenced by no script is not on the product's path; its tests still run. Nothing below has
been moved (owner rule: list first, move never delete; the move itself is a separate step
with `unwanted-things/README.md` updated and `cleanup.sh` for the irreversible parts).

| Path | Reached from a live entry point | Tests importing it | Scripts, CI, compose | Verdict |
| --- | --- | --- | --- | --- |
| `src/evalgate/` (15 modules) | no | 4 | none (the nightly eval workflow runs `tests/evals`, not this CLI) | park, after the owner confirms the eval harness it replaced |
| `src/signals/` (9) | no | 6 | none | park with `refresh/` and `alerts/` as one set (monitoring v1, never wired to a run) |
| `src/refresh/` (14; 3 modules reached) | partly: `replica/schedules.py` imports the cadence helpers | 8 | none | keep the 3 reached modules, park the rest or fold them into `replica/schedules.py` |
| `src/alerts/` (13; 6 reached) | partly: notifications | 7 | none | keep the reached modules; park `digest.py`, `channels.py`, `outbox.py`, `templates.py`, `engine.py`, `freshness.py`, `caps.py` |
| `src/privacy/` (6) | no | 1 | none | park; `people/erasure.py` is the erasure path the product uses |
| `src/quality/` (4), `src/registers/` (5), `src/security/` (5), `src/unitcost/` (3), `src/metrics/` (3), `src/contracts/` (4) | no | 1 each | none | park (release-process tooling from the build phases; `docs/` keeps their reports) |
| `src/crm_writeback/` (7; 5 reached), `src/connectors_client/` (15; 11 reached) | partly | 3 and 8 | none | keep (mail and drive connectors and CRM write-back are product features); `connectors_client/notes.py`, `gmail.py`, `crm_hubspot.py`, `crm_salesforce.py` are unreached adapters to review |
| `src/governance/` (4; 2 reached) | partly | 1 | none | keep the reached two; park `throttle.py`, `account_cost.py` |
| `src/parallel/` (15; 13 reached) | yes (the governor) | 11 | none | keep; `simulate.py` is a tool, fine to park |
| `src/verify/` (7; 5 reached) | yes | 3 | none | keep; `figures.py`, `narrative.py` unreached |
| `src/account_intelligence/rag/` (15; 3 reached) | partly: the retrieval used by Ask | 6 | none | keep the reached modules; `graphrag.py`, `reflection.py`, `synthetic.py`, `metrics.py`, `cache.py`, `guardrails.py` are a second retrieval design that Ask does not use: park after the owner confirms |
| `src/account_intelligence/model_routing.py` | no | 0 | none | park |
| `src/agents/lessons.py`, `src/agents/service.py` | no (the service entry is `production/wiring.py`) | yes | none | `service.py` is the harness's entry (`AbmRunService`) and stays; `lessons.py` to review |
| `src/account_intelligence/worker/nightly.py`, `worker/job.py` | CLI only (`python -m account_intelligence.worker`) | 1 | Cloud Scheduler calls the nightly checks | keep |
| `src/exports/fingerprint.py`, `exports/docs.py` | no | yes | none | review |
| `src/documents/parts.py`, `reindex.py`, `run_child.py`, `sandbox_child.py`, `tables_reingest.py` | subprocess entry points (the sandbox child runs by path) | yes | none | keep: launched as processes, not imported |
| Root: `screenshot_all.js`, `screenshot_all.mjs`, `screenshot_all.spec.ts`, `verification-report.txt`, `run_sql.py` | not code paths | no | no | park (one-off tooling; `scripts/` holds the maintained scripts) |
| Root reports: `BACKEND_AUDIT.md`, `SCREENS.md`, `SYNC.md`, `TECHNICAL_DOCUMENTATION.md`, `TESTING.md`, `DECISIONS.md`, `DEPLOYMENT.md`, `RUNBOOK.md`, `BUILD_LOG.md` | documents | | | keep `README.md`, `PROJECT_STRUCTURE.md`, `CONTRIBUTING.md`, `DEPLOYMENT.md`, `RUNBOOK.md`, `SECURITY.md`; move the audit reports under `docs/audit/` |
| `web/app-design/`, `web/.next-*` | already parked (see `unwanted-things/README.md`) | | | done |
| `docs/` (14 top-level reports plus 30 folders) | documents | | | keep; `docs/parity/`, `docs/research/`, `docs/performance/` are the live design notes of this week |

Duplicates to settle (two implementations of one rule):

* Word rendering: `exports/word/render_cli.py` (python-docx, "interim") and the new
  `render/docx/` (the `docx` npm port the parity work requires). Once the bundle path is wired
  into the build node, the python-docx renderer goes to `unwanted-things/`.
* Workbook rendering: `exports/excel/*.py` (from the snapshot) and `src/parity/excel/*.py` (from
  the bundle). Same resolution.
* Retrieval: `account_intelligence/rag/` (hybrid search used by Ask) and `rag/graphrag.py`
  (unused).
* Count logic for the sheets: `views/tables.py` (file tables), `replica/sheets.py` (live
  sheets) and `src/parity/counts.py` (the formula mirror). The parity module is the one to keep
  once the screens read the bundle.
