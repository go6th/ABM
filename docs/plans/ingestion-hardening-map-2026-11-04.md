# Upload ingestion: current pipeline map and deviations (2026-11-04)

Read-only survey on `feat/final-product` (working tree as it is, uncommitted changes included).
Nothing has been changed. Each deviation is marked **D#**. Decisions needed are under "Questions".

## 1. Map

| Step | Where | What it actually does | Tables / stores |
|---|---|---|---|
| Upload start | `api/routes/documents.py:146` → `DocumentService.start_upload` (`documents/service.py:256`) | Checks name/type/size against the per-file limit, creates `documents` row (`awaiting_upload`), opens a **GCS resumable session** to `quarantine/tenants/{t}/{doc}/raw`. Without a resumable store (local) it returns `PUT /v1/documents/{id}/content?token=` (token-checked stream, `receive`). Optional `audience` = `client` or `owner_only` | `documents` |
| Browser | `web/lib/upload.ts`, `web/lib/upload-store.ts`, `web/components/ws/DocumentDrop.tsx` | Sends the file in `chunk_bytes` chunks with `Content-Range`, 5 tries per chunk, backoff 600 ms·2^n up to 15 s; after a drop asks GCS how much it holds (`bytes */N`) and resumes; keeps the session URL in localStorage keyed by user+name+size+mtime (cleared at sign-out), so choosing the same file again resumes; on 404/410 starts a fresh upload once. Then `POST /complete` **with no body** (no size or checksum sent). Then polls `GET /status` | localStorage |
| Complete | `routes/documents.py:344` → `complete` (`service.py:387`) | Reads the object size from storage metadata; mismatch or over limit → `rejected` and quarantine deleted; else `quarantined` and **Cloud Tasks** `/tasks/document-process` enqueued (`platform/gcp/adapters.py`, `documents_queue`) | `documents` |
| Scan | `_scan` (`service.py:545`), `documents/scan.py` | Type detected from content first (mismatch → `wrong_type`). Then `ScanPort`: `ClamdScanner` (INSTREAM to the clamd **sidecar** in the worker service, staging/prod), `SignatureScanner` (dev/test: EICAR, executables, PDF active content, macros, archive members with script suffixes), `UnavailableScanner` (cloud without CLAMD_HOST). Unavailable → `rejected` reason `scan_unavailable` (fails closed; quarantine object kept). Clean → sha256, moved to content-addressed `tenants/{t}/raw/{sha}` (deduped per tenant) | `documents`, `raw_files` registry |
| Plan + read parts | `_plan`, `read_parts`, `_read_part` (`service.py:609-826`) | Planner and parser run in a sandbox child. Each part, under a lease: zip listing → `workspace.member_roles` → build/owner-only members listed, not parsed → company evidence (`Resolver`) → **redaction** → chunk → `document_passages` + whole typed rows into the table store → `document_members` rows | `document_parts`, `document_passages`, `document_tables` (+ rows), `document_members` |
| Decide | `_decide` (`service.py:827`) | All parts done → `parsed` with `detected_company` (resolved / ambiguous / none); any failed part → document `failed` (`parts_failed`) | `documents` |
| Bind | `confirm_company` (`service.py:1099`) | If `account_id` was given at upload start, the document is bound from the start and the detected company never gates it. Otherwise it waits at `parsed` with `needs_company` until the user confirms, **even when the company resolved** | `documents` |
| Index | `_index` (`service.py:919`) | Only when bound. Pages of passages → `HashingEmbedder` (leak gate) + optional on-server `FastEmbedder` (semantic). Both local, $0. Each batch logged with estimated tokens (chars/4) | `chunks`, `research_file_model_calls` (`task='embed_passages'`) |
| Import | after `indexed`: `_import_deliverables` → `deliverables.import_document` → `workspace_import.import_workspace` (`workspace_import.py:615`) | Workspace zip: reads passages + the zip itself, merges duplicates, then `_write_all` in **one transaction** as the account's owner, under run `file-import-<doc>` (`lifecycle.record_file_import_run`, reused on re-import). Five savepoints: `jobs` (`jobs.repo.import_postings`), `people` (`people.repo.import_people`), `tech` (stack items + detections + platform teams, `agents/production/pipelines.py`), `docx` (sources, facts, signals → news events, sections, coverage tracker; `workspace_docx.write`), `raw` (data/, data/releases/, notes/; `workspace_raw.write`). Loose (non-workspace) workbook → each row as a private fact instead | `account_runs`, `runs`, `job_postings` (+ sentences, classifications), `people`, `person_sources`, `person_profiles`, `tech_stack_items`, `tech_detections`, `platform_teams`, `sources`, `facts`, `news_events`, `org_connections`, `report_sections`, `coverage_rows`, payload store |
| Analysis | `_analyse` → `app/auto_analysis.start` | Once per document (run idempotency key `auto-file:<doc>`); skipped for owner-only. Creates a **full file-mode research run** (`documents/graph.py`: setup → extract → tracker → summarise → internal_file → gate → deliver → finish) | `account_runs`, `usage_events`, ledger |
| Sweep | `api/main.py:211` `_sweep_documents` (sweeper pass) | (a) re-enqueues documents/parts whose lease expired (`documents_orphaned`, `document_parts_orphaned`); (b) `backfill_imports`: up to 10 documents per tenant that are indexed, bound and not marked imported get the **whole** import again | `documents`, `document_parts` |
| View | `replica/sheets.py` (`SHEETS`, 15 sheets + `file_sheet`) | Fixed SQL per sheet; UI, xlsx and CSV read the same definition. "Uploaded files" sheet lists `document_members` with rows imported / merged / not imported | read-only |

Isolation today: every table above has `tenant_id NOT NULL` and **FORCE ROW LEVEL SECURITY**. `tenant_txn` sets `app.tenant_id` from the verified identity only. Owned tables (`config/ownership.yaml`, 0063 "private workspaces") also need `owner_user_id = app_user()` unless the job runs in `system` scope. `replica/isolation.py` runs a nightly cross-tenant probe; `tests/isolation/` holds RLS tests.

## 2. Deviations from the brief

**Tenancy (PR1)**
- **D1** No `org_id` anywhere. The tenant is `tenant_id`, already NOT NULL on every step 3–6 table and enforced by forced RLS. Adding `org_id` would duplicate it.
- **D2** `sheets.py` has no explicit tenant predicate (0 mentions of `tenant_id`). Isolation is RLS only. No test runs every sheet under two tenants.
- **D3** On top of that, rows are private per user (owner policy). "Everyone on the account" in the docstrings effectively means the account's owner. The docstrings also say "uploader and admins" for owner-only, but the RLS policy grants admins nothing.
- **D4** Owner-only takes two forms. (a) Zip members under `Internal/` or `owner/`, or named `*_only.docx` / `*_internal_only.docx`: listed in `document_members` (visible only to the uploader through RLS), never parsed, chunked, embedded or imported. This matches the brief. (b) A whole file uploaded with `audience=owner_only` **is** chunked and embedded (the uploader's Ask can search it). Only import and analysis skip it.

**Workspace marker**
- **D5** Two root markers are accepted, `RESTORE.md` **or** `MANIFEST.json` (a `MANIFEST.json` package also marks imported findings as authoritative). A zip with `deliverables/` and no marker is treated as a loose bundle today: every member is read and every table may import, as facts.

**Import (PR2/PR3)**
- **D6** "Each part commits on its own" is not true. The five parts are **savepoints inside one transaction**: a failure after the savepoints (member counts, commit) loses every part.
- **D7** A failed part is only a counter (`jobs_failed`) plus a log line. The document is still marked imported, so the sweep **never retries it**, and nothing in the UI shows it.
- **D8** When the whole import raises, the sweep retries the **whole upload** every pass with no attempt limit, so a permanently broken file is retried forever.
- **D9** Job postings without a link are already skipped, counted as `jobs_no_url` and shown as "rows not imported" on the member. No reasons are recorded.
- **D10** Idempotency already exists, keyed per document: one reused run `file-import-<doc>`, writers `ON CONFLICT (tenant_id, run_id, <key>) DO NOTHING` (posting_key, person_key, function+category+technology, technology, lower(team), event_key, company_domain+category, ...). It is **do-nothing, not upsert**, so a corrected re-import does not update rows. The brief's key `(org, account, natural key)` drops `run_id`. That would merge rows **across documents and across research runs**, because these tables are shared with research runs and the sheets pick the newest run's rows (`NEWEST_*`). That changes the data model, not just the import. `docx` counts `sources_written` even when the source already existed.

**Order (PR5)**
- **D11** Import runs only **after embedding finishes**. It does not need the embeddings: it reads `document_passages` and the zip, both ready at `parsed`.
- **D12** Company match is not a confidence threshold. It returns resolved / ambiguous / none and never binds by itself. An upload with an `account_id` skips the gate entirely.

**Scan / GCP (PR4)**
- **D13** Upload is already a signed resumable GCS session, and processing is already event-driven: the API's complete endpoint enqueues a Cloud Tasks task. There is no Pub/Sub and no finalize trigger. The sweep is already a safety net.
- **D14** Quarantine is a **prefix in the documents bucket** with a **7-day** lifecycle rule (`infra/modules/gcs/main.tf:108`), not its own bucket with 24 h. The browser sends no checksum and `complete` checks size only.
- **D15** Nothing marks `awaiting_upload` rows abandoned. They stay forever after the object is deleted.
- **D16** ClamAV already runs as a clamd sidecar on the worker in staging and production. Local dev uses `SignatureScanner`, which rejects `.js` (and `.sh`, `.bat`, `.ps1`, ...) inside archives. It does **not** reject `.py`.
- **D17** Scanner down already fails closed, as status `rejected` with reason `scan_unavailable`. There is no `scan_failed` status.

**Analysis / cost (PR6)**
- **D18** Step 5 is not "one Fable run writes a summary". It is a full file-mode research run: one model call per prose batch (`file_extract_facts`), one summarise call, the tracker, an Internal Only Word file, the gate. Models come from `config/models.yaml` routes. I have not confirmed that every call resolves to Fable. It already runs at most once per document; a re-import does not start it again (`backfill_imports` never calls it).
- **D19** No "~1,000-token checkpoint" exists. The existing pause-and-ask pattern is the token guard: stop at `token_guard_multiple` (3×) the estimate, Proceed/Cancel (`agents/graph.py` `token_gate`). Analysis tokens reach `usage_events` / `llm.usage` through the run. Nothing is stored on `documents`.
- **D20** Embeddings are local models ($0, no API). They are already batched, and "tokens" are chars/4 estimates written per batch to `research_file_model_calls`.

**Logs**
- **D21** Logs are JSON, but fields vary: `tenant_id` is almost always present, `document_id` sometimes, `account_id` on index/import only, `import_run_id` and `part` almost never.

## 3. Questions (blocking before PR1)

1. **org_id vs tenant_id.** Recommend: treat `tenant_id` as the org, add no column, and make PR1 (a) add explicit `tenant_id = :t` predicates from the session to every sheet and count query, on top of RLS, (b) add the two-tenant × every-sheet test, (c) fix whatever that test finds. Or do you want a real `org_id` column (and what would it be, if not the tenant)?
2. **Owner-only.** Keep the per-user privacy layer (D3)? And for a whole file uploaded as owner-only (D4b): keep chunking and embedding it for the uploader's Ask, or stop?
3. **Natural keys (D10).** Keep idempotency per document (`tenant, run, key`, changed to a real upsert), or move to `(tenant, account, key)` across documents and runs? The second changes how research rows and uploaded rows coexist.
4. **Markers (D5).** Keep `MANIFEST.json` as a second marker next to `RESTORE.md`?
5. **PR4 shape (D13–D17).** Separate quarantine bucket + 24 h, or keep the prefix and shorten its rule? Keep "API complete → Cloud Tasks" (it already verifies), or add a GCS finalize → Pub/Sub trigger? Rename `scan_unavailable` to a `scan_failed` status?
6. **Step 5 checkpoint (D18–D19).** Use the existing 3× token guard, or build a new fixed ~1,000-token checkpoint? (Note: one checkpoint per 1,000 tokens would interrupt a run many times.)
7. **PR mechanics.** The tree has hundreds of uncommitted files on `feat/final-product`, and I commit only when you say so. Six independent PRs need a clean base. Should I branch each PR from the current tip after you commit the tree, or do you want something else?
