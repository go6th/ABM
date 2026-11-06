# Upload ingestion hardening: decisions taken (2026-11-04)

The owner said "complete the whole task, don't ask me". Where the brief and the code disagreed
(`ingestion-hardening-map-2026-11-04.md`, D1–D21), these are the decisions. Every PR follows them.

| # | Question | Decision | Why |
|---|---|---|---|
| Q1 | org_id vs tenant_id | **`tenant_id` is the org.** No new column. Every new table has `tenant_id NOT NULL DEFAULT app_tenant()`, forced RLS, and the tenant-and-owner policy where the row is owned (0063). New queries state `tenant_id` in their SQL (`db.session.session_tenant`). Log lines carry `tenant_id` (the existing key). | Owner's answer |
| Q2 | Owner-only | Keep the per-user layer (0063). `Internal/` members of a zip: never parsed, chunked, embedded or imported, and listed only to the uploader (pinned by `tests/isolation/test_sheet_isolation.py`). A whole file uploaded as `owner_only` keeps today's behaviour (indexed for its uploader's Ask, never imported, never analysed). | Brief scope is `Internal/` files; no silent change |
| Q3 | Natural key | Upsert keyed on **(tenant_id, file-import run, natural key)**. The file-import run is one per (account, document) (`file-import-<document>`), so this is (tenant, account, document, natural key). `DO NOTHING` becomes `DO UPDATE` with the newest values, so a corrected re-import updates rows. Rows are not merged across different documents or research runs; the sheets already show one row per thing (`replica/current.py` newest-only). | Cross-run merging would change the research data model |
| Q4 | Root markers | Keep `RESTORE.md` and `MANIFEST.json`. Add the fallback: a zip with a `deliverables/` folder and no marker is a workspace, logged `documents.workspace_marker_missing`. | Brief rule plus existing packages |
| Q5 | GCP shape | A **separate quarantine bucket** (Terraform in `infra/`) with a 24 h delete rule; resumable sessions open there. `complete` verifies size and CRC32C (the browser computes CRC32C and sends it). The trigger stays **`complete` → Cloud Tasks** (the brief allows Cloud Tasks; it fires only after verification). The sweep stays as the safety net. A daily job marks `awaiting_upload` rows older than 24 h `abandoned`. A scanner that is unreachable gives status **`scan_failed`** (not `rejected`), and the file is never imported. Local dev and CI scan with the same ClamAV container (compose service); `SignatureScanner` is removed from the upload path. | Brief |
| Q6 | Step 5 cost control | Step 5 keeps its run (`auto_analysis`). Its pause-and-ask uses the existing token guard (`token_gate`). The ~1,000-token checkpoint does not exist and is not built. Analysis input and output tokens and embedding tokens are recorded on `documents` and in the per-tenant usage the admin console reads. | Existing pattern; no new model calls |
| Q7 | PRs | **No commits** (standing rule: commit only on "commit"). Each PR's exact change is saved as a tree snapshot and a patch in `docs/plans/prs/`, reviewed and tested on its own. | The tree holds unrelated uncommitted work |

Extra item (owner, 2026-11-04): **Ask cites the deep-dive sheet.** An answer drawn from an
imported workspace cites the deep-dive sheet row (a link that opens the sheet filtered to those
rows), not the zip. From the row, the user opens the web link. See `ingestion-hardening-ask.md`.

## Decisions taken during the build

- **PR3 upserts.** The research tables are insert-only for the runtime role. The grant says
  "postings are observations" (0010), and `job_postings`, `people`, `tech_*`, `platform_teams`,
  `news_events`, `org_connections` and `report_sections` all grant INSERT and SELECT only. The
  writers therefore stay insert-if-absent on (tenant, the document's file-import run, natural
  key). That already gives identical counts on re-import. Grants were not widened.
- **PR5 company match.**
  - An upload sent to an account is held as `needs_account` only when the file names another
    company (`company.hold_mismatched_for_account: true`).
  - A file that names no company or several proceeds on the account the user picked
    (`hold_unmatched_for_account: false`). Under the strict rule, a workspace zip (its tables
    carry no company evidence) would be held on its own account page, and the account page would
    never fill. That breaks the brief's 60-second done-check.
  - Turning on `hold_unmatched_for_account` restores the strict rule.
  - An upload with no account binds by itself to the uploader's account of the detected domain,
    or is held.
- **PR4 macros.** Stock ClamAV passes Office macros. `documents.scan.StructureGuard` refuses them
  and executables, in every environment, in front of the scanner. It never judges a name, so
  `.py` and `.js` files are accepted.
- **Migrations, in order:**
  - 0106 import status
  - 0107 import retry
  - 0108 document tokens
  - 0109 needs_account
  - 0110 scan_failed / abandoned
