# Archive pages: structured copies and the website viewer, done note (2026-10-29)

Owner request: a posting (about two months on the company's site, then a 404), a news article, a
release, an executive page, a customer story, a filing, a profile, a detection: everything the
research cites is kept in our own archive as a structured JSON record and opened on our own
website like a web archive page, with the original link and the archived link side by side in the
Excel files and the website tables. Nothing is committed.

## What changed

**Structured records (one schema per kind, versioned).** `src/account_intelligence/archive_records.py`
(new, pure) builds and guards the records; `src/jobs/snapshot.py` (new) builds the posting record.
| Kind of link | Schema | Built from | Writer |
| --- | --- | --- | --- |
| job posting | `posting-archive/1` | the posting, its stored text, its sentence pairs, requisition | `jobs/repo.py save_run` (`_archive_entries`) |
| news article, release | `news-archive/1` | the feed item (headline, summary, category, publication date) and its text | `agents/production/payloads.py StorePayloads` |
| any other page read or found | `page-archive/1` | title, text, publisher, date when the page states one | `StorePayloads` |
| person profile | `profile-archive/1` | name, title, headline, location | `people/repo.py` |
| technology detection | `detection-archive/1` | technology, first and last seen, sources | `agents/production/pipelines.py _insert_detections` |

Record fields (all `source: "6th agents"`, `captured_at` ISO time, optional `withheld` list):
* `posting-archive/1`: `url, title, requisition, location, status_at_capture (open|closed|unknown),
  first_seen, last_seen, posted_at, seniority, platforms[], class (label), platform_sentences
  [{platform,text}], description`.
* `news-archive/1`: `url, title, publisher, published_date, category, summary, article_text`.
* `page-archive/1`: `url, kind, title, publisher, published_date, text`.
* `profile-archive/1`: `url, name, job_title, headline, location, text`.
* `detection-archive/1`: `url, technology, first_seen, last_seen, sources, text`.

Rules: no provider, tool, ledger reference or credit figure is ever in a record (the ledger
reference stays only in the database column). Every free-text field passes the guard
(`agents.naming.archive_guard`: the page guard plus the platform denylist; jobs and people use
the denylist alone, they may not import `agents`); a field the guard refuses is blank and named
under `withheld`, never the whole copy. The route applies the page guard again on every read.
Contact details are removed from posting text. A copy is content-addressed over what the page
SAID (not the capture time): an unchanged item of a later research writes nothing, changed content
(closed, new location, reworded text) writes a new copy beside the old one (the table is
insert-only; the newest copy wins; verified with the unique key `tenant, account, url_hash,
payload_ref`). Older `format text` copies and generic JSON bodies stay readable (`record` is null).
The requisition reading is one function (`jobs.snapshot.requisition_of`) the workbook reader
imports; the pattern is read from `config/export_rules.yaml`, not copied.

**API.** `GET /v1/accounts/{domain}/archive?url=` is JSON only now (the old server-rendered HTML
page, which used an inline `<style>` the CSP strips, is gone) and returns the optional `record`;
`&download=true` returns the record as a file. New `GET /v1/accounts/{domain}/archive/list?kind=&
offset=&limit=` (max 200): newest copy per address, newest capture first, with `title, kind,
archived_at, status_at_capture, location, requisition, record_schema`, `total` and `next_offset`.
Registered `tenant_route("read_public","read")`, isolation matrix entry added, other tenant gets
404. Contract and generated client regenerated (`docs/api/openapi.json`,
`web/lib/gen/api.gen.ts`); every new field is optional.

**Website.** `web/app/(client)/accounts/[domain]/archive/page.tsx` + `web/components/accounts/
ArchiveView.tsx`; look in `web/ui/components.css` (`arc-*` classes, no inline style, no style
block). With `?url=`: the Wayback-style page: the capture strip (Archived copy, capture date,
Open/Closed when captured, the dates as a short sequence, "Open original (may no longer exist)"),
a quiet note for a closed posting or a copy older than 90 days, the head with Download JSON and the
Back button, then the layout of the kind: posting (fields grid, platform sentences, full
description), news (headline, publisher, category, publication date, summary, article text),
profile, detection, other page (title and text). Without `url`: the archive index (Title, Kind,
Captured, Status when captured, Location, View and original links, kind filters, Show more). "Archive"
is in the account's Deep dive navigation. The table's archived link opens the website page
(`row.archives` now carries `/accounts/{domain}/archive?url=`; a delivered file's archived link
of any origin, or the older API path, is opened as the same-origin page, `lib/render.ts ownHref`).

**Files.** `exports/readers/pg.py _archive_url` makes the absolute web address
`{PUBLIC_BASE_URL}/accounts/{domain}/archive?url={encoded}` (posting rows, the source register and
the news rows of Sources and Evidence all go through it, so every cited page with a copy gets the
website link). Nothing calls the original address from the server.

## Design (shown before it was built)
Mock: `docs/design/archive-page-mock.html` (self-contained, three views by address fragment) with
`docs/design/archive-page-mock-desktop.png`, `archive-page-mock-mobile.png`,
`archive-news-mock-desktop.png`, `archive-news-mock-mobile.png`, `archive-index-mock.png`,
`archive-index-mock-mobile.png`. The real pages, from the running web app (preview servers
`api-tabs` + `web-tabs`, scratch database `ai_tabs_ui` migrated to 0091, stopped afterwards):
`docs/design/archive-page-real-jobs-table.png` (the Jobs table with the archived link, clicked),
`archive-page-real-closed-posting-desktop.png` / `-mobile.png`, `archive-page-real-open-posting-
desktop.png`, `archive-page-real-news-desktop.png` / `-mobile.png`, `archive-page-real-profile-
desktop.png`, `archive-page-real-detection-desktop.png`, `archive-index-real-desktop.png` /
`-mobile.png`. The corner badge in the real shots is Next's dev overlay. The 34 CSP
"inline style" console messages appear on every page of the dev server including the sign-in
page (none comes from the archive pages: they set no style).

## Files
New: `src/account_intelligence/archive_records.py`, `src/jobs/snapshot.py`,
`web/app/(client)/accounts/[domain]/archive/page.tsx`, `web/components/accounts/ArchiveView.tsx`,
`tests/jobs/test_snapshot.py`, `tests/jobs/test_archive_snapshot_db.py`,
`tests/replica/test_archive_records.py`, `web/tests/unit/archive-pages.test.tsx`, the `docs/design`
files above, this note.
Changed: `src/account_intelligence/archive.py` (`ArchivedCopy.record`, `ArchiveListItem`,
`list_latest`, `list_items`, `LinkArchive.entry_for_record`, copy_from for records),
`api/contract.py`, `api/routes/account_workspace.py`, `replica/sheets.py`, `agents/ports.py`
(`SourceItem.meta`), `agents/production/{payloads,provider,pipelines,wiring}.py`,
`agents/naming.py` (`archive_guard`), `jobs/repo.py`, `people/repo.py`,
`workflow/stages/core.py`, `exports/readers/{jobs,pg}.py`, `docs/api/openapi.json`,
`docs/architecture/loops.md` (regenerated), `web/lib/{render.ts,gen/api.gen.ts}`,
`web/components/accounts/{SheetView,TabView}.tsx`, `web/ui/components.css`, the account page's
navigation, and the tests that pinned the old path or shape (`tests/exports/test_db.py`,
`test_jobs_workbook.py`, `tests/replica/test_link_archive.py`, `tests/agents/
test_production_adapters.py`, `tests/isolation/matrix/matrix.yaml`, `web/tests/unit/
sheet-view.test.tsx`).

## Checks
Backend (scratch database `ai_test_arch`, wrapper with the project's env prefix):
* `pytest tests/replica tests/jobs tests/exports tests/isolation tests/system tests/api tests/people
  tests/agents -m "not eval and not live" -q -p no:cacheprovider`: **2764 passed, 1 xfailed** in 42 min.
* After the last edit (bandit-clean SQL in `archive.py`): `pytest tests/replica/test_link_archive.py
  tests/replica/test_archive_records.py tests/jobs/test_snapshot.py tests/jobs/test_archive_snapshot_db.py
  tests/jobs/test_repo_db.py tests/agents/test_production_adapters.py tests/people tests/isolation`:
  **1301 passed**.
* New and extended tests: snapshot schema and round trip incl. requisition and sentence pairs, guard
  withholds a field, identity ignores capture time; DB: record per posting, old text copies still
  readable and the structured one newest, unchanged posting = one copy, changed content = new copy
  beside the old one (newest wins), index paging and kind filter; route: structured copy, download,
  refused field blanked, list paging newest first, tenant isolation, limit 201 refused, unknown account
  404; news, page, profile, detection records; items_of carries record metadata; export archive URL is
  the web page; isolation matrix entry for `/archive/list`.

Web: `cd web && npx vitest run`: **46 files, 273 tests passed** (17 new in
`tests/unit/archive-pages.test.tsx`: fields, banner, Download JSON, hostile text escaped, unsafe
original not linked, news/profile/detection/page layouts, index, navigation entry, the rewritten
archived link); `npx tsc --noEmit` clean.

Static: `ruff check src tests` clean; `mypy` 697 files clean; `lint-imports` 19 kept, 0 broken;
`interrogate src` 100%; `check_comments`, `check_guarantees` (500 tags) clean;
`gen_loops.py --check` (regenerated once, `docs/architecture/loops.md`), `python -m exports.docs --check`,
`export_openapi.py --check`, `gen_web_client.py --check` all OK. Three files outside this change are not
ruff-formatted (`exports/builder.py`, `exports/change_note.py`, `tests/exports/test_evidence_register.py`);
left as they were.

Browser proof: scratch database `ai_tabs_ui` migrated to 0091; a scratch password set for the seeded
demo user (not recorded here); synthetic snapshots written through the repo's own writers
(`PgPostingRepo._archive_entries`, `LinkArchive.entry_for_record`) for a demo account's two postings plus
a news item, a page, a profile and a detection; the Jobs table's "archived" link was clicked and opened
the archive page; Download JSON answered 200 with `attachment; filename="posting-archive.json"`. Both
servers were stopped afterwards.

## Stays open
* A news article is stored as kind `page` (the table's kind list is fixed by a CHECK and widening it
  needs a migration); the record's schema names it a news article and the web and the index use that.
* The posting archive writes one copy per posting that has stored text, every research; an
  unchanged posting adds nothing, but a posting that reopens with exactly the text of an older copy
  keeps the newer closed copy as the newest (insert-only, no reorder).
* Phase 1 search results are archived with the snippet the search returned unless the page was read
  in full (as before); the record says so only by its text.
* Delivered files built before this change keep their old archived-link address until rebuilt
  (`POST /v1/exports`, free); the table already opens the old API path as the website page.
* The index counts no number beside "Archive" in the navigation (adding `counts.archive` would make an
  older web reject the workspace response, the generated schemas are strict).
* Not deployed; no migration. The page needs the next web and API release together.
