# Ingestion hardening PR2: import status and observability

Migration 0106 adds `import_runs` (one row per uploaded document; a re-import reuses it and counts
the attempt) and `import_run_parts` (one row per part: jobs, people, tech, docx, raw) with status
(pending, running, ok, failed, skipped), rows written and skipped, the first three skip reasons
(a row number and a rule, never a cell), the error class of a failure and the attempts. The
workspace importer (`documents/workspace_import.py`) now creates the run and every part row in
their own short transaction before any part runs (a part with no input is `skipped` at once),
runs each part in its own transaction (marked `running` before, `ok`, `failed` or `skipped` after)
and does the per-member reconciliation in a last transaction, so a late failure no longer rolls
back the other parts and a screen sees a part while it runs; a loose workbook gets a run with no
parts. `documents/import_status.py` is the only writer. The Uploaded files sheet has an "Import"
column (the status of the part each file feeds, e.g. "skipped (12 rows)") and an "Import problems"
chip; the account workspace reply has `import_problems` (max 10) and the account page shows a
banner linking to that sheet. Steps 2 to 5 (scan, read, index, import, analyse, and each import
part) log inside `obs.logging.log_context`, so every line carries `tenant_id` (the org),
`account_id`, `document_id`, `import_run_id` and `part`. To verify on staging: upload a workspace
zip, then open the account's Uploaded files sheet and check the Import column per file and
`SELECT part, status, rows_written, rows_skipped FROM import_runs r JOIN import_run_parts p ...`;
upload a zip whose Job Postings workbook has no links and see "skipped (N rows)" with the banner
absent (skipped is not a problem) while a failed or pending part shows the banner; filter the logs
on `import_run_id` to follow one import. No model call is added; the folder roles, RESTORE.md and
the sheet definition shared by UI, Excel and CSV are unchanged.
