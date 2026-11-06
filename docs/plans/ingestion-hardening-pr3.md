# PR3: Idempotent writers and bounded retry (2026-11-04)

Migration `0107_import_retry` adds `import_run_parts.next_attempt_at` and its index.

## Writers and natural keys

Every step-4 writer is keyed on (tenant, the document's file-import run, natural key). The table
of keys is at the top of `documents/workspace_import.py`. A re-import of the same upload writes
nothing new.

`test_importing_the_same_upload_twice_gives_identical_row_counts` checks this against every
tenant table in the catalog, not a hand-picked list.

**Not done: `DO UPDATE`.** The brief asked for "upsert", but these research tables grant the
runtime role INSERT and SELECT only ("postings are observations", 0010). I did not widen the
grants. The writers stay insert-if-absent, and the brief's acceptance check (identical counts)
holds. A corrected file is a new upload, and the account page reads its run as the newest.

## Bug fixed

A re-import re-added a person who had been erased, through the raw `leadership.json` list. That
list matches people by name, and erasure blanks names. A folded leadership entry is now stored as
another source of the held person (`people.repo.record_folded_sources`, the table's single
writer), so it is known by key even after erasure.

## Retry

- A failed part gets at most 3 attempts. The wait is 5 minutes after the first failure and 10
  after the second; there is none after the third, which is final.
- A part found `running` 30 minutes after its last update counts as a spent attempt.
- The sweep (`documents.deliverables.retry_import_parts`, in `api.main._sweep_documents`) runs
  only those parts (`import_workspace(only_parts=...)`). Successful parts keep their rows and
  attempts. An upload whose import succeeded is never taken again.
- An import that fails as a whole (before or between parts) is retried by `backfill_imports`, now
  bounded the same way (`repo.claim_imports`). It used to repeat on every sweep, forever.

## Analysis

Neither retry path starts the step-5 analysis. The analysis keeps its once-per-document run key
`auto-file:<document>`; PR6 pins this with tests.

## PR2 review fixes, made here

- **Blocking: an import that stopped before or between its parts was invisible or misleading.**
  Now:
  - the parts that never ran are marked failed;
  - the banner lists a run that failed as a whole (part `import`);
  - every file that feeds a part shows "failed" on the Uploaded files sheet;
  - a re-import with no parts clears the old part rows.
- Smaller fixes:
  - `rows_written` counts only `*_written`;
  - an `ok` part that skipped rows shows "ok (N rows not imported)";
  - the "Import" column is last, so existing export column positions don't move;
  - member counts are reconciled as the document's owner;
  - the banner shows each document's latest upload (a re-upload with the same name replaces the
    old one);
  - the `process` and backfill log lines carry the document and account ids.

## Tests

- `tests/documents/test_import_retry_db.py` (4 tests):
  - a failed part is tried 3 times in all, then left failed, and the other parts are untouched;
  - a retried part that succeeds lands, and the run is ok;
  - a part left running by a dead worker is tried again;
  - a whole-import failure is retried at most 3 times.
- `tests/documents/test_workspace_reimport_db.py` (2 tests).
- Two new cases in `test_import_status_db.py` for the blocking review finding.

## Verify on staging

1. Re-run the import of one uploaded workspace: `POST` the backfill, or let the sweep pick it up.
   The account's row counts per table are unchanged.
2. Break one part on a test account, for example a Summary with a bad source address. That part
   alone shows `failed`, and `import_run_parts.attempts` goes 1 → 2 → 3 over about 15 minutes of
   sweeps, then stops.
3. Logs: `documents.import_parts_retried`.
