# Release note: exact copy of an uploaded workspace (2026-11-05, evening)

Release `rel-350a4cdd`: a snapshot of the uncommitted working tree (one folder, branch
`feat/final-product`), built as one Cloud Build of api, worker and web. Carries migration 0111.

## What changes for the user

An uploaded workspace zip now comes back word for word (`docs/audits/ucb-exact-copy-audit-2026-11-05.md`,
proof `docs/audits/ucb-exact-copy-proof-2026-11-05.md`):

- the import stores every sheet and every Word table as written (the deliverable members are no
  longer passed through the Standard redaction; the national-ID pattern no longer eats names);
- every sheet row is a row: nothing folded, linkless archive records kept (migration 0111 lets a
  file-import posting have no address);
- the five files are registered as the account's delivered files; the screens open them as tabs
  from the file itself; an imported run is never materialised or rebuilt;
- Download everything for an imported account ships the files, each workbook as uploaded, each
  sheet as CSV, each Word file's tables as a workbook and CSV, and `Logs/import_report.md`; the
  repair stage does not run for it.

## After the deploy

- Upload `UCB_workspace.zip` again to the ucb.com account: the existing import predates the file
  registration. Until then the download falls back to reading the zip's own deliverables and the
  screens show the native rows.
- The account's domain must match the package's company (ucb.com), or the upload is held as
  `needs_account` and asks for confirmation.

## Revisions

| Service | Before (rollback) | After |
| --- | --- | --- |
| api | ai-staging-api-00126-pvq | ai-staging-api-00127-fhx (b80bab85) |
| worker | ai-staging-worker-00108-r7b | ai-staging-worker-00109-w7d (1f290792) |
| web | ai-staging-web-00079-vhx | ai-staging-web-00080-457 (95829760) |
| migrate | head 0110 | 0110 -> 0111 (ai-staging-migrate-lspl5) |

Rolling the api or worker back below this release while an import made by it exists is safe for
reading; the linkless posting rows (url NULL) are only written by this release.
