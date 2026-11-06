# 07 Documents, parser sandbox and Ask

Verified against the code on 2026-11-03.

See also: [README](README.md) · [01 Architecture and planes](01-architecture-and-planes.md) · [02 Request path and auth](02-request-path-and-auth.md) · [03 Research run lifecycle](03-research-run-lifecycle.md) · [04 Model routing](04-model-routing.md) · [05 Spend and caps](05-spend-and-caps.md) · [06 Data and isolation](06-data-and-isolation.md) · [08 Exports and gate](08-exports-and-gate.md) · [09 Ops](09-ops.md) · [10 Known gaps](10-known-gaps.md)

Another session is changing `src/documents/service.py`, `deliverables.py`, `workspace.py` and the parsers right now. This page cites those files by function name only. The same goes for `src/documents/graph.py`, `sources.py` and `src/account_intelligence/api/routes/chat.py`, which changed while this page was written.

## 1. Upload path (api)

`src/account_intelligence/api/routes/documents.py` is also being edited by the other session, so it is cited by function name here. Routes live under `/v1/documents` (the module's `router`), and their contract is in the module docstring:

1. `POST /v1/documents/uploads` (`start_upload`) declares name, size and type, which are checked before any byte arrives. It returns a resumable object-storage session, or, only where the store has none, `PUT /v1/documents/{id}/content` (`upload_content`) with a one-time token. Bytes go straight to quarantine and never pass through the web app.
2. `POST /v1/documents/{id}/complete` (`complete`) checks the size and enqueues processing.
3. `POST /v1/documents/from-drive` (`import_from_drive`) pulls a Google Drive or OneDrive file into quarantine on the server; from there it is processed like an upload.
4. The remaining routes: status, list, retry of a failed part (`retry_part`), company confirmation (`confirm_company`), estimate, delete, the redaction policy (`get_policy` / `put_policy`) and the authoritative flag (`set_authoritative`). A `members` route for zip members is being added with the zip import.

Admins, analysts and sellers may upload; viewers may not (module docstring, `UPLOADERS`).

Storage keys: quarantine is `quarantine/tenants/<t>/<doc>/raw`; accepted bytes are content-addressed `tenants/<t>/raw/<sha256>`, one object per tenant and hash (`src/documents/service.py`, `quarantine_key` and `raw_key`). Bucket lifecycle rules delete `quarantine/` after 7 days and `uploads/` after 3 days (`infra/modules/gcs/main.tf:108-124`).

Limits come from `config/research_input_modes.yaml`: 20 GiB file fallback (the platform setting wins), 32 MiB browser chunks, accepted types `pdf, docx, xlsx, csv, pptx, txt, md, zip`, and audio only once a transcription vendor is configured (`config/research_input_modes.yaml:11-20`).

## 2. Processing task (worker)

- Cloud Tasks calls `POST /tasks/document-process` on the documents queue. The handler acts as the document's owner, then calls `DocumentService.process`. A deadlock or a held lease answers 503 so the queue retries (`src/account_intelligence/api/routes/tasks.py:160-183`).
- The documents queue allows 20 attempts, 10 to 60 s backoff, because "busy" (503) is a normal answer while a client's parts run (`infra/stack/main.tf:268-276`).
- Pipeline, from the `src/documents/service.py` module docstring and `DocumentService._process`:
  1. Type check from content, malware scan, sha256, then promote out of quarantine.
  2. Plan the parts in the sandbox, then read each part in its own sandbox child: company evidence, redact, chunk, passages.
  3. Once the document is bound to an account, embed and index into the private `chunks` index.
- A document with no account stops at `parsed` until the user confirms the company (`DocumentService.confirm_company`).
- Parts: CSV and text files split at 64 MiB, PDFs at 200 pages. At most 4 parts run at once per client, each holding a 300 s lease (`config/research_input_modes.yaml:25-35`).
- In the cloud, one part runs per Cloud Tasks request, with at most 600 s of indexing per request (`src/account_intelligence/app/container.py:587-591`).
- The worker's sweeper re-enqueues documents whose worker died mid-step (`src/account_intelligence/runs/sweeper.py:23`).
- Each call logs `documents.processed` with the status it reached (`src/documents/service.py`, `DocumentService.process`).

## 3. Malware scan (clamd)

- `scanner_for` returns `ClamdScanner` when `DOCUMENT_SCANNER=clamd` and a host is set. Otherwise it returns `SignatureScanner` in dev/test and `UnavailableScanner` in the cloud, which rejects every upload `scan_unavailable` (`src/documents/scan.py:168-174`).
- `ClamdScanner` speaks clamd `INSTREAM` over TCP. Any error or odd reply is `unavailable`, never `clean` (`src/documents/scan.py:8-11`, `src/documents/scan.py:116-153`).
- `SignatureScanner` (local only) checks for EICAR, executables, PDF active content and Office macros (`src/documents/scan.py:3-7`).
- The worker sets `DOCUMENT_SCANNER = "clamd"`, `CLAMD_HOST = "127.0.0.1"` and `CLAMD_PORT = "3310"` (`infra/stack/main.tf:638-640`). clamd runs as a sidecar container of the worker service (1 CPU, 4 GiB, image `var.clamav_image` pinned by digest) (`infra/stack/main.tf:660-665`, `infra/stack/main.tf:58-63`).
- The app reads the scanner choice in `document_service` (`src/account_intelligence/app/container.py:565-570`).
- Nothing is parsed before the scan passes. An infected file or an unavailable scanner ends `rejected` (`src/documents/service.py`, `DocumentService._scan`).

## 4. Parser sandbox (ADR-0124)

The parser is treated as untrusted. `parse_in_sandbox` / `plan_in_sandbox` run `documents.sandbox_child` in a subprocess (`src/documents/sandbox.py:1-14`, `src/documents/sandbox.py:120`, `src/documents/sandbox.py:159`):

| Protection | Where |
|---|---|
| Empty environment (only `CONFIG_DIR`), Python isolated mode, document as read-only fd 0 (never its path) | `src/documents/sandbox.py:3-5` |
| `RLIMIT_AS`, `RLIMIT_CPU`, `RLIMIT_FSIZE=0`, `RLIMIT_CORE=0`, `RLIMIT_NOFILE` | `src/documents/sandbox_child.py:189-197` |
| Network namespace via `unshare(CLONE_NEWNET)` where the kernel allows it | `src/documents/sandbox_child.py:201-216` |
| Drop root to `nobody` (images run as uid 10001, so it does not apply in prod) | `src/documents/sandbox_child.py:220` |
| seccomp filter (no socket, connect, execve, fork, ptrace, io_uring), fail closed on Linux, plus an audit hook confining reads to code and config | `src/documents/sandbox_child.py:162-185`, `src/documents/sandbox_child.py:234-241` |
| Parent wall-clock timeout, text cap, non-zero exit rejected, `documents.sandbox_applied` logged once per distinct protection set | `src/documents/sandbox.py:5-11`, `src/documents/sandbox.py:77-89` |

- Per-part limits: 1500 s wall clock, 1500 s CPU, 1536 MiB memory (`config/research_input_modes.yaml:37-40`).
- **Residual risk**: hijacked native code can still `open()` any file uid 10001 can read, including the read-only bucket mount `/mnt/blobs` and `/proc/<parent>/environ`. The separate parse Cloud Run job that would close this is proposed, not built (`docs/decisions/ADR-0124-parser-sandbox-untrusted.md:3`, `docs/decisions/ADR-0124-parser-sandbox-untrusted.md:41-47`). The worker does mount the bucket (`infra/stack/main.tf:645`).
- After a deploy, check the log event `documents.sandbox_applied` for `seccomp=on`, `degraded=false`.

## 5. Indexing and retrieval

- Two embedders are configured. `HashingEmbedder` is always on and feeds the leak gate. `FastEmbedder` is an on-server transformer used for retrieval when `DOCUMENT_SEMANTIC_EMBEDDER=fastembed` (`src/account_intelligence/app/container.py:571-578`), which the cloud sets for all services (`infra/stack/main.tf:326`).
- `documents.retrieve.retrieve` is hybrid retrieval:
  - lexical: Postgres full-text search on `document_passages.tsv`, ranked by `ts_rank_cd`;
  - dense: cosine distance on `chunks.semantic_embedding`;
  - fusion: reciprocal rank fusion from `account_intelligence.rag.retrieval`, keeping the top 10;
  - scope: only the caller's own accounts (`src/documents/retrieve.py:1-14`, `src/documents/retrieve.py:30-33`, `src/documents/retrieve.py:125`).
- Ask uses it (`src/account_intelligence/replica/answers.py:633`), and so does the on-demand source search (`src/documents/sources.py`, which imports `documents.retrieve.retrieve`).

## 6. File-mode runs (`src/documents/graph.py`)

- A run created with `input_mode = 'file'` runs `FileGraphFactory` instead of Workflow v1 or the ABM graph (`src/account_intelligence/api/routes/tasks.py:126-133`, `src/documents/graph.py`, `is_file_run`).
- Graph: `setup -> extract -> tracker -> internal_file -> gate -> deliver -> finish` (`src/documents/graph.py`, `build_file_graph`).
  - `setup` fails the run when documents are missing or not indexed.
  - `extract` records facts graded Internal, each citing its document and passage.
  - `tracker` fills all 33 rows. Rows that need public sources are `not_searched_file_only`.
  - `internal_file` writes the Internal Only Word file.
  - `gate` is the pre-delivery gate ([08](08-exports-and-gate.md)) (`src/documents/graph.py`, module docstring).
- No node calls a data broker; the ledger holds zero-cost document reads only. Public enrichment is a separate linked company-mode run (`src/documents/graph.py`, module docstring).
- Terminal mapping: `completed` gives COMPLETED; a setup failure gives FAILED with a full refund; anything else gives PARTIAL (`src/documents/graph.py`, `FileGraphFactory.target`).

## 7. Ask (chat), high level

- `POST /v1/chat/ask` streams server-sent events: `status`, `text`, `thread`, `answer`, `done` or `error`. Model text is buffered until the whole answer passes the client output scrub and the domain scans (`src/account_intelligence/api/routes/ask.py:1-11`, `src/account_intelligence/api/routes/ask.py:151`).
- The answer rows come from the account's research, tracker, previous deliverables, uploaded files (Internal) and the user's own mail and drive (`src/account_intelligence/api/routes/ask.py:11-13`).
- `replica.router` classifies each question as `tabular`, `mixed` or `prose`:
  - `tabular` skips retrieval and offers the `describe_table` / `query_table` tools at temperature 0;
  - `mixed` offers tools and retrieval;
  - `prose` uses retrieval only.
- `run_python` is offered only behind the tenant's `run_python` flag (`src/account_intelligence/api/routes/ask.py:15-25`). It runs in the same sandbox lockdown (`documents.run_child`) with a timeout and no network, and logs only the code's sha256 (`src/documents/run_python.py:1-15`).
- Threads: `POST/GET /v1/chat/threads`, `GET/DELETE /v1/chat/threads/{thread_id}` (`src/account_intelligence/api/routes/chat.py`, `new_thread`, `threads`, `thread`, `remove_thread`). Model routing for Ask is covered in [04](04-model-routing.md).

## 8. In progress (do not rely on)

- Workspace-zip import (`src/documents/workspace.py`, untracked) assigns roles to the members of an uploaded research-workspace zip. It comes with migration 0102 (`migrations/versions/0102_document_members.py`). Another session owns both.

## Code vs docs

- `docs/open-items.md` OI-IM-5 says a clamd deployment "is needed before production" (`docs/open-items.md:173`). The staging stack already declares the clamd sidecar and `DOCUMENT_SCANNER=clamd` (`infra/stack/main.tf:638-665`).
- ADR-0124 lists "Drop to nobody" as a protection. In production the images run as uid 10001, so it records `not_root` and never applies (`docs/decisions/ADR-0124-parser-sandbox-untrusted.md:34`).
