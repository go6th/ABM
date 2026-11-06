# Event-loop audit (2026-11-03, fix prompt E task 3, ADR-0130)

Scope: every `async def` in `src/` that runs synchronous I/O or CPU work likely over 50 ms on the
event loop. The worker's liveness probe killed it at least four times on a blocked loop (YAML
parse per table cell, certificate fetch, archive zip, ledger parse; all four were already fixed
before this audit). `src/obs/stalldump.py` diagnoses a stall in production; the guard against new
ones is `tests/system/test_event_loop.py` (a hermetic company research run in-process; it fails
when one callback spends more than 50 ms of the loop thread's own CPU, and names the code).

Fix patterns: **startup** = a cached loader warmed by `account_intelligence.app.warm.warm_caches`
in a thread from the `api.main` lifespan; **cache** = cached by file state (`os.stat` mtime and
size, the `grading.config` pattern); **thread** = `asyncio.to_thread` (the `exports/builder.py`
`build_all` pattern).

## Fixed in this change

| # | Site | What blocked | Fix |
|---|------|--------------|-----|
| 1 | `src/account_intelligence/api/routes/exports.py` `create_export` (kind pdf) | LibreOffice conversion and `pdftotext` (subprocess waits, up to minutes) and `doc_checks` on the loop | thread (conversion, text extraction, checks, `pdf_package`) |
| 2 | `exports.py` `create_export` (files, csv) | `files_package` / `csv_package` (openpyxl load of every xlsx in `check_package`) | thread |
| 3 | `src/account_intelligence/replica/file_tabs.py` (5 sites), `replica/sheets.py:605` | uncached `load_export_config()` (6 YAML files, ~110-130 ms) per request | cache (`default_export_config()`) |
| 4 | `src/account_intelligence/connectors/settings.py` `load_providers_config` (callers `agents/production/credit.py`, `workflow/stages/spend.py`, `runs/lifecycle.py` `close_spend`, `api/routes/health.py` `/readyz`, `agents/production/payloads.py`) | providers.yaml parse + validation (~20 ms) per call | cache by file state |
| 5 | `src/account_intelligence/api/routes/library.py` `library_upload` | `inspect_package` unzips/rezips up to 100 MB | thread |
| 6 | `src/documents/gate.py` `check` | per client file: `client_text` unzip + regex, `scan_package`, `third_party_scan`, `rows_lost` | thread (one `scan` per file) |
| 7 | `src/account_intelligence/api/routes/account_workspace.py` `download_deliverables` | in-memory deflate zip of every file | thread |
| 8 | `src/agents/graph.py` `build` node (`assert_client_clean` over every member of every built client file) | regex name scan, up to 0.9 s per run (measured by the new test) | thread |
| 9 | `src/agents/production/delivery.py` `_internal` / `texts` | python-docx build of the Internal Only file; unzip of a deliverable | thread |
| 10 | `src/agents/production/wiring.py` `AbmGraphFactory.build` | LangGraph build + compile (~55 ms per slice) | thread |
| 11 | `src/account_intelligence/platform/gcp/adapters.py` `GcsPayloadStore.put` / `get` | level-9 `gzip.compress` / `gzip.decompress` of every payload | thread |
| 12 | `src/exports/store.py` `_missing` | `google.api_core` import per call | cache |
| 13 | first calls of cached loaders on the run path (`provider_terms`, `name_digests`, client-output patterns, skill config, export config, model catalogue, prices, task efforts, batch config, jobs config, export rule texts, parallel config, research cache config, data-class policy, the research graph modules) | ~2 s in all on the first research of a process | startup (`warm_caches`) |

## Measured residue, accepted (the test excludes or tolerates these, with the reason in its docstring)

- **Garbage collection** on the loop thread (up to ~0.13 s per collection in the test process).
  Measured with `gc.callbacks` and subtracted from the callback that triggered it; reported in the
  failure message. Not addressable by `to_thread`.
- **GIL starvation by offloaded work**: a callback that takes > 50 ms of wall time but not of loop
  CPU waited for the GIL held by a thread (one long regex or zlib call in C, e.g. the name scan
  and the export builder). Tolerated up to `EVENT_LOOP_STARVED_S` (1 s). A process pool for the
  builder and the name scan would remove it; not done here.
- **FastAPI 0.142 lazy route setup** (~90 ms once per application object and route, on its
  first request) and **asyncio debug mode's own bookkeeping** (source lines read for every task
  created): excluded by the test.

## Not fixed (lower severity; listed for the next pass)

| Site | What | Severity |
|------|------|----------|
| `src/account_intelligence/connectors/service.py:438, 490, 646, 745, 756` | `connector.parse` (json + pydantic over a provider body) per page and poll | medium (per page) |
| `src/exports/store.py` `put_if_absent` / `put_set` | sha256 of each deliverable body and read-back hash | medium (once per build) |
| `src/parity/service.py:346` `bundle` | `json.loads` of the full bundle per request | medium |
| `src/people/repo.py:523, 610` | `json.dumps`/`json.loads` of a run's claims payload | medium (once per run) |
| `src/account_intelligence/replica/table_export.py:121, 158` and `file_tabs.py` `iter_rows` | per-cell `safe_cell` regex while streaming a sheet; the async generator never awaits | medium-high for very large tables |
| `src/account_intelligence/replica/downloads.py:340` `_fill` | uncached `load_skill_config().scope` once per download job | medium |
| `httpx.AsyncClient` built per call: `replica/web_tools.py:280`, `api/routes/ask.py:640`, `api/routes/chat.py:346`, `connectors_client/drive_files.py`, `maildrive.py` | SSL context + certifi per client (6-30 ms) | low-medium |
| `src/account_intelligence/api/routes/runs.py:488` (ETag json + sha256 per poll), SSE json per event, `jobs/repo.py` per-posting hashing, `spend/upload.py:318` sha256 of an upload | small, bounded | low |
| Small uncached YAML reads: `runs.py:828 progress_config`, `replica/live.py:96`, `admin_console.py:790 _skill_version`, `workflow/handlers.py:46`, `datamodel/pins.py:45`, `agents/contracts.py:122` | 1-16 ms each | low |

Already fixed before this audit (verified): providers.yaml per table cell (`lru_cache`), grading
config per fact (file-state cache), the task-token certificate fetch (`api/routes/tasks.py`, thread
with a 20 s timeout), the Download-everything archive (`replica/downloads.py`, every zip write in a
thread), the ledger parse (`grading/adapters.py:215`, thread).
