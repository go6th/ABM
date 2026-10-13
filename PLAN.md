# Research File feature: plan

Date: 2026-10-13. Branch `feat/research-files` (from `feat/durable-runs`, which carries the
uncommitted research cache and durable-runs work). Read-only inspection; no code changed before
this file.

## What exists and what is reused

| Need | Where it lives today | Reuse decision |
| --- | --- | --- |
| Object storage | `platform/ports.py PayloadStore` (`put`/`get` of whole bytes, gzipped): `platform/hermetic.py LocalPayloadStore` (dev volume `/tmp/payloads`, shared by api and worker), `platform/gcp/adapters.py GcsPayloadStore` | Not reusable for GB files: whole-bytes API and gzip. A new streaming `BlobStore` port (append part, compose, ranged read, delete) with a local-volume adapter on the same `payloads` volume and a GCS adapter (compose of part objects). |
| Migrations | `migrations/NNNN_*.sql` plus `migrations/versions/NNNN_*.py`; last is 0050 | New 0051, linted by the skill's `lint_migrations.py`; down file included. |
| Tenancy | `tenant_txn()` sets `app.tenant_id`; every table `tenant_id NOT NULL DEFAULT app_tenant()`, ENABLE and FORCE RLS, policy `TO app_rw` with USING and WITH CHECK; `tests/isolation/matrix/matrix.yaml` probes every table and route | Same pattern on every new table; matrix entries for new tables, routes and the job. |
| Run ledger | `connectors/ledger.py` (`tool_calls`, write-ahead `started` events) for provider calls; `model_reply_log` (migration 0049) for model calls | The file pipeline is not a research run, so it gets its own write-ahead tables keyed by file and chunk, with the same idea: a `started` row before work, a finished row before the next unit. |
| Model router | `platform/llm.py AnthropicLlm` (tier to model through `config/models.yaml`, effort and prompt caching per task), `llm/reply_cache.py` (cost pricing from `models.yaml`) | Reused: tasks map to tiers in a new `config/research_files.yaml` (classification and extraction to the `extractor` tier, which routes to the cheapest configured model, summaries to `synthesis`). Pricing through `llm.reply_cache.price`. |
| Retrieval | `rag/` (hybrid retrieval over the pgvector `chunks` table, bound to accounts and embeddings) | Not reused for v1: no embedding model is configured. File chunks get a Postgres full-text index (`tsvector`); retrieval ranks with `ts_rank`. |
| Upload safety | `spend/upload.py` (quarantine, fail-closed scanner, sha256 dedupe), `connectors_client/quarantine.py` | Same principles: the raw file is stored before parsing, sha256 dedupe per tenant, content never logged. No malware scanner exists locally (the existing one fails closed and is not configured); files are never executed or served back as HTML. |
| API, web | Route catalog `api/policy.py`, typed contracts `api/contract.py`, OpenAPI snapshot, generated web client `web/lib/gen`; web pages under `web/app/(client)`, sidebar in `components/ws/WorkspaceShell.tsx` | New routes registered in the catalog and contract; a new "Research files" page in the web app. |

Installed for this work (2026-10-13): `pypdf` 6.20.1 and `pyarrow` 26.0.0 (`uv add`). Already
present: `openpyxl` (xlsx read-only streaming), `lxml` (docx, pptx, html), `Pillow` (image
metadata), `charset-normalizer` (encoding detection). Not available: tesseract (no OCR engine) and
ffmpeg; the platform's only model provider has no audio transcription.

## Design

* **Tables (0051)**: `research_files` (metadata and status, sha256 unique per tenant, lease for
  one worker), `research_file_uploads` and `research_file_upload_parts` (resumable upload),
  `research_file_chunks` (each parsed unit: location, content, content hash, tsvector),
  `research_file_findings` (audit report rows: severity, check, location, message, fix),
  `research_file_state` (per-file accumulator checkpoint written with each chunk),
  `research_file_model_calls` (write-ahead `started` row, then the reply with tokens and cost;
  cache keyed by task, model and chunk hash across all files of the tenant).
* **Upload**: init, PUT part (request body streamed to the store, part hash recorded), GET upload
  state (which parts arrived), complete (parts composed while hashing; dedupe by sha256; the file
  row is `UPLOADED` before any parsing).
* **Processing**: one worker per file (lease), plugin per detected type, each unit written with its
  findings and the accumulator checkpoint in ONE transaction before the next unit is read; resume
  seeks to the next unit (CSV byte offset, sheet and row, PDF page, archive member). Statuses:
  UPLOADED, PARSING, AUDITED, ANALYZED, PARTIAL (with reason), FAILED (nothing usable).
* **Audit**: deterministic checks in code; models only for section summaries and question
  answering. Findings never change the data.
* **Analytics**: streaming column profiles (Welford moments, min and max, missing, distinct up to
  a cap, exact top values up to a cap, a deterministic bottom-k sample with its row numbers,
  pairwise Pearson correlation for up to 12 numeric columns); document entities and topics by
  code; charts built from stored profiles, each carrying the rows or pages it came from.
* **Cost**: estimate before any model step; approval above `approval_limit_usd`; cache by chunk
  hash; prompt cap with splitting; every call logged.
* **Unsupported types** (unknown, OCR without an engine, audio and video without a transcription
  model): stored, status PARTIAL with the reason, never rejected.
* **Delete**: removes the raw object, the parts and every derived row of the tenant's file, and
  records one insert-only deletion event (hash and size only).

# Two research input modes: plan

Date: 2026-10-13. Branch `feat/research-files` (kept: the working tree is shared with another
session, so no branch switch). Nothing committed; no live paid run.

## Migrations

* `0052_research_input_modes`: `documents` (quarantine, scan, parse, index status; account
  binding; detected company; authoritative flag), `document_passages` (redacted chunks with
  location and heading path), `document_redactions` (the redaction map; originals kept),
  `document_policies` (per-tenant redaction tier), `run_documents` (run to document link),
  `run_gate_results` and `leak_checks` (insert-only gate evidence), `fact_reevaluations`
  (insert-only), `document_deletions` (insert-only). Columns: `account_runs.input_mode`,
  `chunks.passage_seq/location/heading_path/embedding_model`, `fact_evidence.document_id/
  document_location` with evidence kind `document`, `deliverables` untouched (the internal file
  lives in `internal_deliverables`). Every new table: `tenant_id` and `account_id`, forced RLS,
  isolation matrix entries.

## Files

* New package `src/documents/`: config, scan (signature scanner, clamd adapter, fail closed),
  sandbox (parser subprocess with no network, resource limits), router and parse (reuses the
  research files parsers), redact (Strict, Standard, Minimal), chunk, embed (port plus a local
  hashing embedder), company resolution, repo (single writer), pipeline, upload (signed PUT),
  extract (untrusted boundary, verbatim span check), leakcheck (n-gram and embedding
  similarity), gate (pre-delivery gate for both modes), graph (Mode B graph factory), internal
  deliverable.
* Changed: `api/routes/runs.py` (input modes), new `api/routes/documents.py`, `tasks.py`,
  `policy.py`, `contract.py`, `container.py`, `grading/repo.py` (document fact writer),
  `connectors/ledger.py` (zero-cost document read record for tracker proof),
  `tracker/model.py`, `tracker/completion.py`, `config/tracker_v1.yaml`, `config/grading.yaml`.
* Web: New research screen with the two-option switch, uploader with per-file status, company
  confirmation, enrichment toggle with estimate, source labels, internal view.
