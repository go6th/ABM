# 08 Exports, the delivery gate and workspace repair

Verified against the code on 2026-11-03.

See also: [README](README.md) · [01 Architecture and planes](01-architecture-and-planes.md) · [02 Request path and auth](02-request-path-and-auth.md) · [03 Research run lifecycle](03-research-run-lifecycle.md) · [04 Model routing](04-model-routing.md) · [05 Spend and caps](05-spend-and-caps.md) · [06 Data and isolation](06-data-and-isolation.md) · [07 Documents and sandbox](07-documents-and-sandbox.md) · [09 Ops](09-ops.md) · [10 Known gaps](10-known-gaps.md)

## 1. The export builder (`src/exports`)

`ExportBuilder.build_all` produces the standing client set: Account Summary and Sources and Evidence as Word files, and People, Jobs and Tech as workbooks, plus the workspace zip (`src/exports/builder.py:1-20`, `src/exports/builder.py:1128`). It works in this order (`src/exports/builder.py:3-17`):

1. Read every row from **one** snapshot (`PgContentReader` inside a `REPEATABLE READ, READ ONLY` transaction).
2. Map rows to render models (pure).
3. Render: Word through a sandboxed subprocess, workbooks in process.
4. Verify each file from its bytes: leak scan of every member, row keys re-read equal to the rows selected, document checks, and optionally the LibreOffice PDF and recalculation checks.
5. Build the workspace zip.
6. Store the set put-if-absent in one transaction.

- No model, network or clock is used to produce a file (`src/exports/builder.py:18-20`). A rebuild therefore costs no model spend.
- Missing reader, store or renderer gives status `not_configured` and reason `builders_not_configured` (`src/exports/builder.py:1130-1146`).
- Any unexpected exception becomes a failed build named by its class, never a crash loop (`src/exports/builder.py:1165-1177`).
- Storage (`src/exports/store.py:1-15`):
  - objects are content-addressed at `<tenant>/<account>/<run>/deliverables/<sha256>.<ext>`, written with `put_if_absent`, which refuses to overwrite different bytes (`src/exports/store.py:84`);
  - a repeated build of the same content returns the stored rows and writes nothing;
  - `deliverables` is INSERT and SELECT only.
- Wiring (`src/exports/wiring.py:1-19`, `src/exports/wiring.py:57-82`): `build_export_builder` picks the Word renderer from `EXPORT_RENDERER`. `EXPORT_RENDER_CHECKS=on` adds the LibreOffice checks.
  - The app default is `python-interim` (`src/account_intelligence/app/container.py:530`, `src/account_intelligence/app/container.py:715`), and the cloud sets the same (`infra/stack/main.tf:343`).
  - The cloud sets `EXPORT_RENDER_CHECKS = "on"` (`infra/stack/main.tf:324`); the api and worker images install LibreOffice and poppler (`docker/worker.Dockerfile:63`, `docker/api.Dockerfile:43`).

### Word renderers: two different things

- **Export builder renderer.** `SubprocessWordRenderer` runs `python -I -m exports.word.render_cli` (python-docx) with a cleared environment, its own process group, a timeout, bounded output and one re-run (`src/exports/word/renderer.py:1-16`, `src/exports/word/renderer.py:55-59`). The `node` option looks for `src/exports/word/node/render.js`. That directory does not exist, so choosing `node` gives `NotConfigured` and every build ends `builders_not_configured` (`src/exports/word/renderer.py:62-69`, `src/exports/wiring.py:8-10`).
- **`render/docx`** is a TypeScript package on the `docx` npm library (`render/docx/package.json`). It is the Word renderer of the *output parity* bundle (`src/parity/render.py:7-8`), built into the worker image at `/opt/render-docx` (`docker/worker.Dockerfile:34-41`, `docker/worker.Dockerfile:79`). The parity bundle is off unless `PARITY_BUNDLE=on` (`src/account_intelligence/config.py:589-595`), and the Terraform stack does not set that variable (`infra/stack/main.tf`).

## 2. Withhold, do not fail: per-file rules in the builder

- A file that cannot be made clean is left out of the set, and the rest are delivered. `FileHeld` carries `kind:rule` (`src/exports/builder.py:244-251`).
- File-scoped structural rules: `row_cap`, `missing_narrative`, `missing_tracker` (`src/exports/builder.py:237-240`).
- Content rules that withhold the offending file (`_held_rule`): leak or restricted term, data-tool name or host, the cost word, stale phrase, excluded name, banned dash, orphan citation, or a renderer failure (`src/exports/builder.py:274-283`).
- Error codes map to `LeakError`, `BannedContent` and so on in `_ERROR_OF` (`src/exports/builder.py:103-140`).
- `_render_all` records each held file (`src/exports/builder.py:507-524`), and the manifest carries `files_withheld` as `kind:rule` strings (`src/exports/builder.py:923`, `src/exports/models.py:909`).
- The package scanners live in `exports.verify`: `scan_package` (every docx/xlsx/zip member through the client-output rules), `doc_checks`, and the third-party scan of data-tool names, hosts and the word "credits" (`src/exports/verify.py:1-18`, `src/exports/verify.py:75`, `src/exports/verify.py:679`, `src/exports/verify.py:771`).

## 3. The pre-delivery gate (one owner, withhold per file)

`PreDeliveryGate.check` in `src/documents/gate.py` is the single gate for company and file runs (`src/documents/gate.py:1-49`, `src/documents/gate.py:246`, `src/documents/gate.py:257`).

- **One rule, one owner.** The builder enforces the format rules on files it made. The gate trusts that record by integrity: the deliverable has a `snapshot_hash` (or is a published library copy) and its stored SHA-256 equals the bytes read back. Such a file is not re-scanned for dashes or terms; any other file is scanned whole (`src/documents/gate.py:10-17`, `src/documents/gate.py:148-160`).
- **Checks:** `tracker_rows`, `fact_citations`, `client_dashes` (Word only), `client_terms`, `leak_check`, `internal_label` (file mode), `deliverable_set` (company runs: all five files), `rows_dropped`, `client_output`, `third_party_domains` (`src/documents/gate.py:19-46`).
- **Run-level vs file-level.** `FILE_CHECKS` are `client_dashes`, `client_terms`, `third_party_domains`, `rows_dropped`, `leak_check` and `client_output` (`src/documents/gate.py:100-110`).
  - A run-level failure holds every file: PARTIAL `gate_failed`.
  - A file-level failure holds that file only: PARTIAL `files_withheld`, and the rest are delivered (`src/documents/gate.py:3-8`).
  - A file the builder itself withheld is reported through `builder_withheld` and does not count as missing for `deliverable_set` (`src/documents/gate.py:39-43`, `src/documents/gate.py:112-125`).
  - Defects go to `run_gate_results`, and Resume reruns the gate (`src/documents/gate.py:8`).
- **ABM graph:** a held file gives `error_code = "files_withheld"` and the run continues to `deliver`; `gate_failed` goes to `finish` (`src/agents/graph.py:160`, `src/agents/graph.py:1486-1505`).
- **Workflow v1:** `deliver` maps a gate result starting with `withheld` to `files_withheld` (`src/account_intelligence/workflow/stages/core.py:300-303`).
- **Leak check** (`documents.leakcheck.LeakChecker`): fails on any shared 8-word run between a client file and a private passage (public text excluded), or on a client paragraph of 12+ words with cosine similarity >= 0.90 to a private passage. Only fingerprints, scores and document ids are recorded (`src/documents/leakcheck.py:1-19`, `config/research_input_modes.yaml:82-86`). The product's own earlier deliverables uploaded back are not private material (`src/documents/gate.py:28-32`).

## 4. `POST /v1/exports` (rebuild and package)

- `POST /v1/exports {domain, run_id?, kind, table?}` with `kind` `files`, `csv` or `pdf` (`src/account_intelligence/api/routes/exports.py:1-11`, `src/account_intelligence/api/routes/exports.py:94`).
- For `files` and `pdf` it calls `builder.build_all` again (`_deliverables`) (`src/account_intelligence/api/routes/exports.py:77-91`). This is how files are rebuilt for free after a fix: no new research and no model call.
- Blocked reasons:
  - `delivery_disabled` (`src/account_intelligence/api/routes/exports.py:112`);
  - `tables_withheld` while the materialised tables are not ready (`src/account_intelligence/api/routes/exports.py:128`);
  - `build_failed` (`src/account_intelligence/api/routes/exports.py:140`);
  - `doc_checks_failed` / `pdf_not_configured` for PDFs.
- Builder-withheld files are left out of a `files` package (`src/account_intelligence/api/routes/exports.py:142`).
- Downloads: `GET /v1/exports/{package_id}/files/{index}` serves a delivered package only. The object key must belong to the session's tenant, and the download is audited (`src/account_intelligence/api/routes/exports.py:8-11`).

## 5. Workspace repair stage (`src/exports/repair`)

- Purpose: every table of a workspace is de-duplicated, entity-tagged, dated from the source, filled or explicitly marked, and checked before it is zipped. It is account-neutral, and all account facts arrive in a `WorkspaceProfile` (`src/exports/repair/__init__.py:1-5`). The spec is `docs/repair/workspace_repair_spec.md`.
- Order is fixed: postings, technology, people, news, sources, searches, signals, then coverage (`src/exports/repair/pipeline.py:1-7`). `RepairResult` holds the tables, the acceptance checks and a markdown report (`src/exports/repair/pipeline.py:43-50`).
- Live use: "Download everything" builds `Tables/` from the repair stage, and `Logs/` holds the repair report and the dropped-row log (`src/account_intelligence/replica/downloads.py:1-16`, `src/account_intelligence/replica/downloads.py:290-307`). The worker task is `POST /tasks/download-everything` (`src/account_intelligence/api/routes/tasks.py:232`).
- `replica.repair_guard` installs the client-file rules for the packager, because `exports.repair.package` may not import `replica` or `agents` (`src/account_intelligence/replica/repair_guard.py:1-13`).
- Offline: `scripts/repair/regress.py` repairs an existing "Download everything" zip, optionally against a reference workspace. It reads files only and spends nothing (`scripts/repair/regress.py:1-16`, `src/exports/repair/offline.py:1-8`).

## Code vs docs

- `docs/open-items.md` OI-BLD-1 says "No deliverable builder or production quality gate exists" (`docs/open-items.md:51`). That is false today: see sections 1 and 3.
- `docs/open-items.md` V-EXP-2 says "the production image has no LibreOffice" (`docs/open-items.md:25`). Both Dockerfiles install it (`docker/worker.Dockerfile:63`).
- "render/docx" is not the export builder's renderer. The builder uses python-docx in a subprocess; `render/docx` serves only the parity bundle.
