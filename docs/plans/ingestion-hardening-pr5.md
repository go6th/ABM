# Ingestion hardening PR5: import before index, company match first (2026-11-04)

## Description

PR5 reorders the document pipeline so the account page fills before any embedding is written. The
company is matched first (`documents.service._match`, using `documents.company.Resolver`): an upload
sent to an account proceeds only when the file's resolved company is that account's domain; an upload
sent to no account is bound to the owner's account of the company's domain when there is one. Anything
else (no company, ambiguous, below `company.bind_min_confidence`, or another company than the
account) is held in the new status `needs_account` with the detected company shown; nothing is imported
or indexed until the user picks an account (`POST /v1/documents/confirm-company`, which now also
releases a held upload bound to the same account and can move it to another). A document that is
`parsed` and bound then runs `_import_step` (the deliverables import, its own leased step; status
`indexing` so the sweep covers it), enqueues itself, and the next task embeds (`_index`) and then
analyses. The import reads `document_passages` and the zip, never the vectors. A zip with `deliverables/`
holding a deliverable file but no `RESTORE.md`/`MANIFEST.json` is treated as a workspace (parent of the
`deliverables/` folder is the root) and logs `documents.workspace_marker_missing` once per document.
Ask adds `notices: ["Some files are still being indexed; search may miss them."]` to the answer when a
bound document of the account is not yet `indexed`. Migration `0110_needs_account` (down_revision `0106`;
re-chain on merge) adds the status to `ck_documents_status`.

Switches: `company.bind_min_confidence` (2.0 = the resolver's `min_score`, so every "resolved" passes),
`company.hold_unmatched_for_account` (true; false trusts the account an upload was sent to, the way to
go back if the rule proves too strict on staging), `DocumentService.index_in_own_task` (true in
production; false lets one `process` call run import and embedding, used by the older service tests).

## Staging verification

1. Deploy, run migration 0110 (`alembic upgrade head`), confirm `\d+ documents` shows `needs_account`.
2. On an account page, upload the account's own workspace zip (with `deliverables/`). In the log
   (Cloud Logging, filter `jsonPayload.tenant_id=... `), note the times of:
   - `documents.processed` with `state=quarantined` -> the upload completing (the `complete` call),
   - `documents.imported` (the import step finished),
   - first `documents.indexed_page`, last `documents.indexed_page`, `documents.indexed`.
   Definition of done: `documents.imported` is within 60 s of the upload completing, and before
   `documents.indexed`. Reload the account page at that moment: People, Jobs, Tech stack, Signals and
   Sources are non-zero while the file list still says "Indexing". Ask during that window carries the
   notice line.
3. Upload a zip without `RESTORE.md` but with `deliverables/`: it imports, and the log has one
   `documents.workspace_marker_missing` for that document.
4. Upload a file about another company to an account: the card says "Needs account" with the detected
   company, nothing imported (account counts unchanged), no `documents.imported` line. Pick the account
   in the card: `documents.imported` then `documents.indexed` follow.
5. Upload without an account a file naming a company that has an account: it binds and imports by
   itself. Upload a file naming no company: it waits as "Needs account".
6. Watch `documents.needs_account` log lines (state/reason) for how often the rule holds real uploads;
   if too many, set `company.hold_unmatched_for_account: false` in `config/research_input_modes.yaml`.
