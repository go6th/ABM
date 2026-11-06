# PR1: Tenant isolation of the deep-dive sheets and account-page counts (2026-11-04)

Decision (owner, 2026-11-04): the org is the existing `tenant_id`. No `org_id` column and no
migration. Defaults kept until the owner answers: the per-user privacy layer (0063) stays, and an
owner-only whole-file upload keeps today's behaviour.

## What changed

Every query in `replica/sheets.py` and the account-page counts query in `replica/workspace.py` now
names its tenant in its own SQL, on top of row level security. Every row source read by account
or by run carries `tenant_id = :t`. Every lookup tied to such a row (evidence of a fact, the run
of a tool call, the document of a member, the newest-row probes in `replica/current.py`) matches
that row's `tenant_id`. `:t` is the tenant the transaction was opened for:
`db.session.bind_tenant` now records the verified caller's tenant on the session, and the new
`db.session.session_tenant(session)` returns it. It never comes from a request body. A session
with no tenant raises, so the query fails closed. `sheets.params` takes `tenant_id` as a required
keyword, and `sheets.read`, `sheets.hidden_ids` and `table_export.rows` (the CSV and Excel
exports) pass it. So the UI, Excel and CSV still read one sheet definition. The helper reads of
the file-shaped sheets (`delivered_run`, `_materialised_under`, `_prefetch`) are scoped the same
way.

Internal/ members of a zip were already never parsed, chunked, embedded or imported. Their
`document_members` row is owned by the uploader, so RLS lists it to the uploader only. The new
test pins this behaviour; the code was not changed.

New test `tests/isolation/test_sheet_isolation.py`:
- **Same workspace into two tenants.** Tenants A and B each get an account on the same domain,
  and the same workspace zip (with an added `Internal/` member) goes through the production
  path. Canary rows fill the sheets an upload cannot (web searches, calls, mail and drive, the
  five file-shaped sheets), so every sheet has rows on both sides.
- **Every sheet, with RLS on** (all 15 live sheets and all 5 file-shaped sheets):
  - A's rows carry no row id of B;
  - A and B see the same totals;
  - B's account or run reads empty;
  - B's account is not found at all.
- **Every sheet, with RLS off** (a superuser, A's tenant bound): B's account and run still read
  empty, and so do the account-page counts. So the explicit predicates hold on their own.
  Removing one predicate (Jobs) made this fail with 640 leaked rows.
- **Internal/ member:**
  - listed only to the uploader, not to the account owner or another user of the client;
  - never a passage, chunk, table or fact.

Three existing tests that bind sheet SQL by hand now also bind `:t`.

Files:
- `src/account_intelligence/db/session.py`
- `src/account_intelligence/replica/sheets.py`
- `src/account_intelligence/replica/workspace.py`
- `src/account_intelligence/replica/current.py`
- `src/account_intelligence/replica/table_export.py`
- `tests/isolation/test_sheet_isolation.py` (new)
- `tests/documents/test_workspace_zip_import_db.py`
- `tests/documents/test_deliverables_import.py`

## Not covered (outside "sheets and counts")

These account-page reads still rely on RLS alone:
- the narrative, key findings, runs, open job and gap queries of `workspace()`;
- `archive.lookup` behind `attach_archives`;
- the delivered-workbook tabs (`file_tabs`), which read the stored file by the caller's tenant.

## How to verify on staging

1. Deploy the api.
2. Open an account page and its deep-dive sheets as a normal user. Counts and rows should be
   unchanged.
3. Export one sheet as CSV and one as Excel, and check both download.
4. Watch the logs for `PermissionError: no tenant bound` or `UndefinedParameter` on `:t`. Expect
   none.
5. Run the nightly isolation check from the admin console. Expect Passed.
6. Request `/v1/accounts/<another client's domain>/sheets/people`. Expect 404.
