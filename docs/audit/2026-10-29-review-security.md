# Security review: change set 7e0ad095 to the working tree (2026-10-29)

Reviewer: independent, read-only. Scope: `git diff 7e0ad095` (219 files) plus the untracked files (`archive_records.py`, `jobs/snapshot.py`, `ArchiveView.tsx`, the archive page, tests). Held against `SKILL.md` section 2 and `references/03-tenancy-isolation-security.md`.
Nothing was run against a database, a server or a paid service. Where a claim is marked PROVEN it was reproduced with `.venv/bin/python -I` on pure functions; the scripts are in the session scratchpad (`n1.py` to `n6.py`). Everything else is read from the code and cited by file and line.

## Findings table (ordered by severity)

| # | Severity | Area | Finding | Where |
| --- | --- | --- | --- | --- |
| F1 | High | Client leakage / privacy | The archive keeps and serves profile copies (name, title, headline, location, profile URL) for every person found, including tenant-excluded, out-of-scope, "not a person" and merged duplicates. Tenant exclusions (TN5) are not applied on the archive routes. | `people/repo.py:344-376`, `people/pipeline.py:271-277`, `account_workspace.py:557-662`, `archive.py:334-364` |
| F2 | High | Privacy erasure | Per-person erasure and tenant deletion do not reach the archive. `link_archive` is insert-only (no UPDATE/DELETE for `app_rw`), `erase_person` ignores it, and the archived bodies in the payload store are never deleted (`raw_payload_objects` is "blocked"). Personal data survives erasure and is still served. | `people/erasure.py`, `migrations/0086:51-57`, `config/deletion.yaml:36-37`, `migrations/0091` |
| F3 | High | Rule 6 (provider names) | The `WORD_NAMES` exemption turns the "Parallel" check from deny-by-default into allow-by-position. PROVEN: `Parallel`, `Source: Parallel`, `Parallel returned 4 results.`, `provider: parallel`, `pulled via parallel`, `PARALLEL`, `Parallel's API` were all caught before and none is caught now. The pre-delivery gate and every web surface use this check. | `agents/naming.py:48-56, 96-119, 144-152`; gate at `agents/graph.py:1211` |
| F4 | High | Availability / DoS | `EXPORT_RENDER_CHECKS=on` is now set in infra for API and worker. The LibreOffice conversions (`subprocess.run`, 120 s timeout, 2 attempts, per file, 5 files) run synchronously inside `ExportBuilder.build_all`, which is `async` and calls `assemble` with no thread. A slow or hung conversion blocks the event loop (the 2026-10-28 liveness-shutdown class), and the timeout kills only the `soffice` launcher, not `soffice.bin`. | `exports/builder.py:725-751, 301`; `exports/verify.py:174-213`; `infra/stack/main.tf:290-293` |
| F5 | Medium | Client leakage | `row["archives"][*].href` (the archived-copy link beside each table link) is built from the raw link, then the guard runs on `cells`/`links`/`link` only. A link to a provider domain is dropped from `links` but its archive href (`...archive?url=https%3A%2F%2Fapi.parallel.ai...`) is returned. PROVEN. | `replica/sheets.py:905-966`, `account_workspace.py:452, 464-477` |
| F6 | Medium | Rule 6 (another account) | The new archive routes (and the cell/tab withholding) use `scrub_client_text`, which has no run-level forbidden terms: other accounts' names, excluded companies. The exports pass those (`ctx.restricted`, `apply_exclusions`); the archive serves up to 200,000 characters of verbatim page text per copy without them. | `account_workspace.py:536-552`, `exports/readers/exclusions.py`, `exports/readers/common.py:134-147` |
| F7 | Medium | Data integrity / poisoning | `ArchiveResolver` attaches a LinkedIn profile to a named executive by exact name match against a search-result title, and takes "current employer = this account" from the free-text company part of that title. A same-name person, or anyone who writes the account into their own headline, becomes the listed profile and a current employee. | `people/archive_links.py:60-110`, `agents/production/people_links.py:30-60` |
| F8 | Medium | Migrations | Downgrade of 0089 re-adds `ck_people_level` as `NOT VALID`. A NOT VALID check is still enforced on every updated row, so after rollback any UPDATE of a person with level `senior_director`, `associate_director` or `other` fails, including the erasure tombstone update. | `migrations/0089_people_fields.down.sql:17-21` |
| F9 | Low | Privacy | Legacy `format=text` posting copies (and the fallback when a snapshot cannot be built) store the raw posting text without `strip_pii`, and the write-time guard differs by path (`default_guard` is the denylist only). Read-time scrubbing covers the web, nothing else reads the bodies today. | `jobs/repo.py:218-296`, `workflow/stages/core.py:181`, `people/repo.py:358`, `archive.py:185-192` |
| F10 | Low | Injection (defence in depth) | `Book.body` and `Book.link` write `cell.hyperlink = <any string>`. All current callers pass `Url`-typed values, but the `Url`/`Href` validators (`^https?://\S+$`) accept a trailing newline, `user:pw@` credentials and bidi control characters. PROVEN. | `exports/excel/common.py:331, 362`; `exports/models.py:18-30`; `exports/word/contract.py:26-42` |
| F11 | Low | DoS (regex) | `_vendor_hits` copies `text[:m.start()]` for every match: quadratic in the number of "Parallel" tokens. PROVEN: 840 KB of hostile page text takes 1.9 s on the event loop; 5 MB would take about a minute. New records are cut to 200,000 characters (0.1 s); legacy copies and the exported/pre-delivery text are not. | `agents/naming.py:108-118` |
| F12 | Low | DoS | `/archive` returns the whole stored text uncapped for legacy copies; `/archive/list` reads up to 200 bodies per request (16 in flight) only to show four fields, then runs `count(DISTINCT)` plus `DISTINCT ON` over the whole account each call; offset up to 1,000,000. No per-user limit on these reads. | `archive.py:334-399`, `account_workspace.py:616-662` |
| F13 | Low | Migrations | 0088 and 0089 drop and re-add CHECK constraints in the validating form (ACCESS EXCLUSIVE plus full scan of `job_posting_classifications`, `people`, `person_profiles`); the comments call them "expand only". 0088 down relabels `heuristic` rows as `rule` (provenance misstated). 0086 down and 0090 down drop the tables and orphan the payload bodies. | `0088_posting_heuristic.sql:8-10`, `0089_people_fields.sql:17-23, 33-36`, `0088...down.sql:3` |
| F14 | Low | Isolation consistency | `tech_stack_items.owner_user_id` defaults to `app_user()` and the insert does not name the account's owner, unlike `link_archive`. A write in system scope has no owner (NOT NULL failure, stack silently missing); a write by a different acting user would give the rows to that user, not the account's owner. | `migrations/0090:12`, `agents/production/pipelines.py:559-575` |
| F15 | Low | Policy consistency | The JSON download (`?download=true`) is audited as `read`, not `export` as the deliverable and CSV/xlsx downloads are. The cell route answers 422 for a provider-domain `href` instead of dropping it (the page route drops it). | `account_workspace.py:47-52, 600-612, 505-519` |
| F16 | Low | Isolation of the converter | LibreOffice runs as the app user inside the API and worker containers with full egress and the metadata server reachable, no `ulimit`, no separate process group, on tenant-influenced text. Environment is cleared and the profile is private (good). Input is our own generated files, so exposure is low today. | `exports/verify.py:184-207` |
| F17 | Info | Repo hygiene | `.claude/launch.json` (tracked, modified) hard-codes `postgres:dev` superuser DSNs, `ALLOW_UNAUTHENTICATED_TASKS=1` (refused outside dev/test by `tasks.py:86`) and sources `.env`. | `.claude/launch.json` |
| F18 | Info | Parsing | `file_tabs` opens delivered workbooks with openpyxl without `defusedxml` and its own lxml reads use `huge_tree=True`. Mitigated: only the builder's own deliverable rows are read, sizes are bounded before opening, `resolve_entities=False`. | `replica/file_tabs.py:219-222, 270-284, 353-358` |
| F19 | Info | Cost exposure | `MAX_AUTO_RESUMES` 2 to 5 with research caps off. Not a security defect; recorded under OWASP LLM10 because a crash loop now re-runs five times. | `replica/auto_resume.py:61` |
| F20 | Info | Observability | `stalldump` writes stack text (source lines, no values) to stderr outside the JSON field allow-list. Acceptable; note for the log scanner. CSV downloads now get the 60-minute transfer timeout. | `obs/stalldump.py`, `web/lib/proxy-body.ts:23` |
| F21 | Info | Test gaps | No test for: tenant exclusion vs archive; two users in one tenant on the archive routes (owner scoping is by policy, untested here); erasure vs archive; the word-name exemption at the positions in F3; the render-check path on the event loop. | `tests/replica/test_link_archive.py`, `tests/agents/test_naming_words.py` |

Counts: Critical 0, High 4, Medium 4, Low 8, Info 5.

Checked and found sound (details at the end): RLS on the two new tables, grants, owner scoping, IDOR and payload-key traversal on the new routes, tenant from token, security headers, Content-Disposition, React escaping and `safeLink`, formula injection, SQL building, SSRF, config regex time bounds, logging.

---

## Details

### F1 High: the archive ignores tenant exclusions and scope status

Evidence.
- `PgPeopleRepo._archive_entries` (`src/people/repo.py:344-376`) writes a `profile-archive/1` copy for every person in `record.people` with a name or title and a link. It skips only `internal_only` and persons with no link. It does not look at `p.classification.status`.
- The pipeline keeps excluded people as rows: for a tenant exclusion it builds `Classification(status="excluded", reason="tenant_exclusion")` and still appends the `Person` with name, title, headline and location (`people/pipeline.py:271-295`). Same for `not_a_person_name`, `out_of_scope`, and merged duplicates.
- The copy is recorded with `title=p.name` (`repo.py:373`) and listed by `GET /v1/accounts/{domain}/archive/list?kind=profile` (`archive.py:334-364`, route `account_workspace.py:616-662`), then served whole by `GET .../archive?url=`. Role `read_public` includes `viewer`.
- Phase 1 also archives every search result with its title (`agents/production/payloads.py:56`); a LinkedIn result title is "Name - Role - Company | LinkedIn", so a tenant-excluded person's name and role also appears in the index as the title of a `profile` row.
- The exports apply `apply_exclusions` at render time (`exports/readers/exclusions.py`, GUARANTEE `test_exclusions_hold_at_pull_and_render_time`). The archive routes never call it. An exclusion added after the research is not applied to anything in the archive.

Failing scenario. The tenant lists "Jane Doe" under excluded people (settings). A research runs; the People List correctly omits her. Any viewer of the tenant opens Account, Archive, "Profiles": her name, title, headline, location and LinkedIn URL are listed and openable. Same for a person excluded for "security" titles, or a person excluded after the delivery.

Fix.
1. Write side: in `_archive_entries` skip every person whose status is not `in_scope` or `review`, and skip `merged_into` rows; do not archive Phase 1 results whose title parses as a person page (`people.archive_links.parse_profile_title`) unless the resolved person is listed (store those under a kind the index never lists, e.g. internal `resolver_source`).
2. Read side (needed anyway for exclusions added later): in both archive routes load the tenant's `ExclusionFilter` and drop/blank any `profile` row whose name or URL it excludes (same function the exports use: `tenancy.settings.ExclusionFilter.excludes_person`).
3. Purge: a one-off job (eraser role) for existing `profile` rows of excluded people.
4. Tests: planted excluded person, assert 404 on `/archive?url=` and absence from `/archive/list`.

### F2 High: erasure does not reach the archive

Evidence.
- `people/erasure.py` nulls fields on `people`, `person_profiles`, `person_posts` only. `link_archive` holds the person's URL, `title=name` and a payload body with name, title, headline, location (`people/repo.py:358-376`). The table is granted `SELECT, INSERT` only (`0086:56`), so no app code can null or delete a row; only `tenant_eraser` via `erase_rows`, which is tenant-wide and needs an approved deletion request.
- The payload body is content-addressed in `tenants/<t>/accounts/<a>/archive/<sha>.json.gz`. `config/deletion.yaml:36` lists `raw_payload_objects: no object delete adapter`, so even a tenant deletion removes the index rows (`0091`) and leaves the bodies. The `0091` header says the deletion "removes them with the other derived data"; for the bodies that is not true.
- After a person is erased, `/archive/list` still shows the name (title) and `/archive?url=` still serves name, title, headline, location.
- Smaller point: `erase_rows` runs under the caller's RLS context. If the deletion request session is not in system scope, `link_archive` rows owned by other users' private workspaces are not deleted, and the loop reports "already empty" (`tenancy/lifecycle.py:339-358`). Same for the other owned tables, but this migration adds two more.

Failing scenario. A data subject asks for removal (DPDP/GDPR). `erase_person` runs. Her profile copy and any search-result copy naming her stay readable by every viewer of the tenant, and survive a tenant deletion as orphan objects.

Fix. (a) Do not store the name in `link_archive.title` for profile rows; store the person key. (b) Add an erasure path: a reviewed SECURITY DEFINER function owned by the eraser role that deletes (or tombstones) `link_archive` rows by `(account_id, person_key)`, plus an object-delete adapter for the payload keys (the missing adapter is the real blocker). (c) Call it from `PgPeopleRepo.erase`. (d) Until the adapter exists, correct the `0091` comment and `deletion.yaml` so they say the bodies remain. (e) Confirm the deletion session sets `set_system()` and add a test that a second user's archive rows are deleted.

### F3 High: the "Parallel" exemption is allow-by-position

Evidence. `WORD_NAMES = {"parallel"}` (`naming.py:48`). `_vendor_hits` (`naming.py:108-118`) reports a hit only if (a) the word, any case, is followed by one of the `_PRODUCT_WORDS`, or (b) the capitalised form appears after a character that is not in `_SENTENCE_END`. `_SENTENCE_END` contains `:` `.` `;` quotes, brackets and newline, and the test is skipped when nothing precedes the word.

PROVEN (old code from `7e0ad095` against the new, `n6.py`):

| text | old | new |
| --- | --- | --- |
| `Parallel` | hit | pass |
| `Source: Parallel` | hit | pass |
| `Parallel returned 4 results.` | hit | pass |
| `provider: parallel` | hit | pass |
| `pulled via parallel` | hit | pass |
| `PARALLEL` | hit | pass |
| `Parallel's API` | hit | pass |
| `Data from Parallel.` | hit | hit |

These are the forms an attribution cell, a model sentence or a label takes ("Source: Parallel", a single-word cell, a sentence that starts with the vendor). The vendor was the data broker named in rule 6; `Parallel` is `p_search`'s display name.

Who calls it: `assert_client_clean` at the pre-delivery gate (`agents/graph.py:1211`, `views/packages.py:107, 170`), `scrub_client_text` for every web table cell, tab and archive field, `source_label.source_link` (`replica/source_label.py:128`). Note the export reader's own `leak_hits` (`exports/policy.py`) uses the denylist and `restricted` terms, not these names, so the `naming` check is the main provider-name net.

Can an attacker-controlled page make a vendor name slip through? The page cannot add the word to the product's own text, but a page (or the model) that says "Parallel" in any of the positions above is passed, so the exemption does not depend on the attacker: it simply no longer protects those positions. Homoglyphs, zero-width characters and `Parallel` inside `parallel-search` also pass; they did before the change (not new).

Fix. Invert it: keep matching `parallel` in every case and position, and exempt only a short list of English collocations, e.g. `in parallel`, `run(s|ning)? in parallel`, `parallel (processing|computing|lines?|universe|to|with)`, `parallel(ism|ize|ise)`. Add the cases above as tests. Also normalise NFKC and strip zero-width characters before matching.

### F4 High: render checks block the event loop and orphan LibreOffice

Evidence. `infra/stack/main.tf:290-293` sets `EXPORT_RENDER_CHECKS = "on"` for the shared env block (API and worker). `exports/wiring.py:57` then uses `LibreOffice()` for `pdf` and `recalc`. `ExportBuilder.build_all` is `async def` (`builder.py:725`) and calls `self.assemble(...)` directly (`builder.py:751`), which runs `_render_all` -> `_verify` -> `LibreOffice.pdf_text` / `recalculated` -> `subprocess.run(..., timeout=120)` up to twice per file (`verify.py:178-213`), for 2 Word and 3 Excel files. No `to_thread` anywhere in `builder.py`. Worst case about 20 minutes with the loop blocked; /healthz and Cloud Tasks heartbeats stop. This is the failure documented in `docs/guide/06-known-problems.md` and `obs/stalldump.py` (four liveness shutdowns on 2026-10-28), and the stall dump will name this frame.

Second defect: `subprocess.run(timeout=)` kills the direct child. On Debian `soffice` starts `oosplash`, which starts `soffice.bin`; the grandchild is not killed (the Word renderer does it correctly with its own process group, `exports/word/renderer.py`). A timed-out conversion leaves a running soffice.bin writing into a deleted temp directory, and the retry starts a second one.

Failing scenario. A Jobs workbook of several thousand rows with 32,000-character descriptions: recalculation exceeds 120 s, the loop is blocked for 240 s, the instance is killed mid-run, the run is resumed (up to five times now, F19).

Fix. Run the whole `assemble` in `asyncio.to_thread` (or at least both converters); start the converter with `start_new_session=True` and kill the group on timeout; add `ulimit`/cgroup memory limit; bound the number of concurrent conversions; test that `/healthz` answers during a 5 s conversion.

### F5 Medium: archive hrefs bypass the cell guard

Evidence. `attach_archives` (`replica/sheets.py:936-966`) builds `href = "/accounts/<d>/archive?url=" + quote(url, safe="")` for every link that has a copy. In the route the order is `attach_archives` (line 452), then `_client_page` (464-477), whose `scrub_rows` removes provider-domain links from `links`/`link` but never looks at `archives`, and the rest-of-response guard excludes `rows`. PROVEN (`n5.py`): a row with link `https://api.parallel.ai/v1/tasks/abc` ends with `links: {}`, `link: None` and `archives.url.href == "/accounts/acme.com/archive?url=https%3A%2F%2Fapi.parallel.ai%2Fv1%2Ftasks%2Fabc"`. A copy exists for such a URL because Phase 1 archives every item URL (`payloads.py:56`) and the guard design assumes provider-host URLs do occur.

Fix. In `scrub_rows`, run `third_party_domain_hits`/`tool_name_hits` on each `archives[*].href` (decoded) and delete it; better, call `attach_archives` after the scrub on the surviving links only. Also scrub `fills`/`cut` keys (they are tokens, low risk).

### F6 Medium: archive pages skip the run's forbidden terms

Evidence. Exports build `restricted` and `forbidden_terms` (other accounts' names and domains through the tracker's shared rule, excluded companies) and withhold any text that names them (`readers/common.py:134-147`, `readers/exclusions.py`, `quote_withheld`). `_withhold_leaks` (`account_workspace.py:536-552`) calls `scrub_client_text`, which knows provider terms, tool names, name digests and the platform denylist only. The archive serves verbatim page text for any URL of the account and the account's list of copies, including pages that name another account of the same tenant (and, in private workspaces, another user's account) or a company the tenant excluded.

Fix. Compute the same forbidden set the export reader uses for the account (other accounts of the tenant that the caller may not see, tenant excluded companies) and pass it as `extra_terms` to `client_hits` in the archive routes; withhold the field the way the route already does.

### F7 Medium: name-only profile match with a self-declared employer

Evidence. `candidates_for` (`people/archive_links.py:84-110`) creates a `RawPerson` from any archived `profile` page whose title name equals the mentioned name, with `employer_domain=account_domain` and `is_current=True` whenever the title's company text contains the account's brand word (`_names_brand`). Titles of LinkedIn pages are written by the profile owner. Identity needs only `names_equal`. The page URL comes from the host check in `archive.link_kind`, so the host cannot be spoofed, the content can.

Failing scenario. The reading step quotes "John Smith, CIO" in a release. The search results hold three "John Smith - ... | LinkedIn" pages; the first whose title names the brand wins: the wrong John Smith becomes the listed profile and current employee, shown in the People List with a working link.

Fix. Require a second independent signal before attaching a profile (same title keywords as the mention, or the page URL also returned by a people-source record), never set `is_current` from a title string, mark such rows `review`, and say in the sheet that the profile was matched by name.

### F8 Medium: 0089 down blocks erasure after a rollback

`0089_people_fields.down.sql:17-21` re-adds `ck_people_level` as `NOT VALID`. PostgreSQL still enforces a NOT VALID check on every row version written. After the downgrade, a person with `job_level` in (`senior_director`, `associate_director`, `other`) cannot be updated at all, including the tombstone update `people_erasure_only` allows. Fix: in the down script first map the wider levels to a level the old check allows (record the old value in `classification_json`), or leave the wider check in place on downgrade. Test: downgrade then erase a `senior_director`.

### F9 Low: raw posting text and guard defaults at write time

`PgPostingRepo._text_entry` (`jobs/repo.py:280-296`) records the stored posting text as the copy when a snapshot cannot be built; that text has not been through `strip_pii` (the snapshot path does it, `jobs/snapshot.py:94`). `PgPostingRepo(...)` in `workflow/stages/core.py:181` and `PgPeopleRepo._archive_entries` (`people/repo.py:358`) use `default_guard` (denylist only) while the ABM path passes `archive_guard`. The route re-scrubs on read, so nothing leaks today. Fix: pass `archive_guard` everywhere, strip contact details in the fallback copy, and have `copy_from` strip emails/phones from text copies.

### F10 Low: link sinks rely on upstream validation

PROVEN (`n2.py`): `exports.models._url` and `exports.word.contract._href` accept `https://a.com/x\n`, `https://user:pw@a.com/` and a URL containing U+202E; they refuse `javascript:`. `Book.body` and `Book.link` set `cell.hyperlink` with no scheme check. The `Word` writer `relate_to(href, ...)` likewise. Python `$` matches before a trailing newline; use `\Z` or `fullmatch`. Fix: one `safe_http_url()` helper (scheme http/https, no userinfo, no controls/bidi, length cap) used by the models, the Word contract, `Book.body`, `Book.link` and `table_export._link_cell`. The web side is already right (`web/lib/render.ts safeLink`, `ownHref`).

### F11 Low: quadratic scan on hostile text

PROVEN (`n4.py`): `client_hits("we work in Parallel. " * n)`: 105 KB 0.06 s, 420 KB 0.52 s, 840 KB 1.9 s. Cause: `text[: m.start()].rstrip(" \t")` per match. Fix: look back at most a few characters (`text[max(0, m.start()-8):m.start()]`) or iterate with `str.rfind`. Hostile pages control the number of matches.

### F12 Low: archive read limits

`copy_from` returns `data["text"]` whole for non-record copies; the response model has no max length. `list_items` loads 200 JSON bodies of any size to show status/location/requisition; consider storing those four fields in `link_archive` columns at write time (also lets the index sort/filter in SQL). `list_latest` counts and sorts the whole account for each page. The routes have no per-user throttle. Fix: columns for the index fields, cap response text (offer download for more), rate-limit the group.

### F13 Low: migration locks and downgrade loss

0087 is expand-only (nullable columns; `carried integer NOT NULL DEFAULT 0` is metadata-only on PostgreSQL 11 and later). 0088 and 0089 are not: `DROP CONSTRAINT`/`ADD CONSTRAINT CHECK` validate under ACCESS EXCLUSIVE on `job_posting_classifications` (a table with a row per posting and pass), `people`, `person_profiles`. Use `ADD CONSTRAINT ... NOT VALID` then `VALIDATE CONSTRAINT`, or run in the maintenance window and fix the "expand only" wording. 0088 down changes `heuristic` rows to `rule`, which claims a deterministic rule decided a class a heuristic decided. 0086/0090 down are destructive by design; the payload bodies of 0086 stay unindexed and unerasable (F2).

### F14 Low: owner of `tech_stack_items`

`link_archive` takes the account's owner (`archive.py:247-254`: `coalesce((SELECT owner FROM accounts ...), app_user())`). `tech_stack_items` relies on `DEFAULT app_user()` (`0090:12`) and `_insert_stack` does not name an owner. Fix: same coalesce, or attach `fill_owner('account')` as 0063 does for older tables; add a matrix test for a teammate resuming the run.

### F15 Low: audit class and 422

`GET /archive` is catalogued `read` (`account_workspace.py:50`); with `download=true` it returns a file and should be audited like the other downloads (`audit_class="export"` through `audit_action`, as `download_deliverables` and the CSV route do). The cell route passes `href` through `_client_value`, so a provider-domain link fails the whole cell with 422 where the page route drops the link; drop it instead.

### F16 Low: converter runtime

`LibreOffice._convert` is well built (fixed argv, cleared environment, private profile, temp dir, timeout), but it runs in the service container with the service account reachable and unrestricted egress. Add `--safe-mode`/`-env:` options that disable network and macros, run with `RLIMIT_AS`/`RLIMIT_CPU`, and keep the images pinned (`libreoffice-calc` is unpinned like the rest, DL3008).

### F17 to F21 Info

- F17: remove the superuser DSN and `ALLOW_UNAUTHENTICATED_TASKS=1` from the tracked launch file, or move the file out of git.
- F18: install `defusedxml` (openpyxl then uses it) and drop `huge_tree=True`; the bound `MAX_XML_BYTES` is read from the zip directory, which Python honours when decompressing.
- F19: with caps off, five automatic resumes of a crash loop; log a spend estimate on each automatic resume.
- F20: nothing to do; list `Stall dump:` in the log scanner's canary test.
- F21: add the tests listed in the table.

---

## Checked and found sound

**Migrations (item 1).**
- `link_archive` (0086) and `tech_stack_items` (0090): `tenant_id NOT NULL DEFAULT app_tenant()`, FKs to `tenants` and `(tenant_id, account_id)`, `ENABLE` plus `FORCE ROW LEVEL SECURITY`, one policy `TO app_rw` with `USING` and `WITH CHECK` on `tenant_id = (SELECT app_tenant())` and owner-or-system, identical to the 0077 pattern. Grants are `SELECT, INSERT` only plus sequence `USAGE`. No `public_reader` access. No SECURITY DEFINER added. The database proves `url_hash = sha256(url_normalized)` and http(s) on both URLs. Matrix entries added for both tables and all three routes (`tests/isolation/matrix/matrix.yaml`).
- 0091: `GRANT DELETE ... TO tenant_eraser` only; `erase_rows` keeps `SECURITY DEFINER`, owner `tenant_eraser` (NOLOGIN, NOBYPASSRLS), `search_path = public, pg_temp`, `REVOKE ALL FROM PUBLIC`, execute to `app_rw`, hard-coded allow-list now equal to `config/deletion.yaml` (the test ties them). `%I` quoting for the table name, `%s` of a bounds-checked integer.
- 0087, 0088, 0089 column and grant changes are consistent with the erasure trigger: `employments` is in the erased-profile check, in `people_erasure_only`'s argument list and in `PROFILE_FIELDS`.
- Alembic chain 0085 to 0091 consecutive; `EXPECTED_REVISION = "0091"`.

**Routes (item 2).**
- `authorize` (role, membership re-check, route catalogue, audit `read` row) is in front; tenant comes from `ident.tenant_id` only; there is no tenant, account id or payload key in any request field. Account lookup is `SELECT id FROM accounts WHERE domain = :d` under `tenant_txn`, so RLS (tenant and owner) scopes it; the domain is regex-checked first. Another tenant's or another user's account gives 404; `lookup` and `list_latest` filter by `account_id` under the same RLS.
- `payload_ref` is written only by server code (`archive_key`, `payload_key`, `text_ref`). Even so the route calls `require_owned(ident.tenant_id, payload_ref)` (rejects `..`, `//` and other tenants' prefixes), and the list route uses the same check per row.
- Query parameters are bounded: `url` 8 to 2048, `limit` 1 to 200, `offset` at most 1,000,000, `kind` checked against a set, `col` against `^[a-z][a-z0-9_]*$`, `n` at most 1,000,000, `filter`/`sort` 40 characters and validated against the sheet's own filters and columns. All SQL is parameterised (`text()` with bind parameters; `CAST(:k AS text)`).
- `Content-Disposition` uses fixed names (`posting-archive.json`, `archived-copy.json`), no user input. API responses carry `nosniff`, `frame-ancestors 'none'`, `Cache-Control: no-store` (`main.py:402-414`). GET only: no CSRF or step-up surface. The three new routes are in `ROUTE_POLICIES` through `register`, role set from `read_public`.
- The withholding behaviour is correct for cells, tabs and archive fields (a refused field is blanked and named under `withheld`; the page always loads) apart from F5 and F15.

**Injection (item 4).**
- `ArchiveView.tsx`, the archive page, `SheetView.tsx`, `TabView.tsx`, `cell-fill.ts`, `render.ts`: no `dangerouslySetInnerHTML`, every value is a React text node; every external `href` goes through `safeLink` (http/https only, no userinfo, no controls, no backslash, 2048 cap); `Archived` accepts only hrefs that start with `/accounts/`; `ownHref` rewrites an archive address of any origin to a same-origin relative path (so it cannot be used to leave the site); fill tokens are whitelisted; `download` href is built with `encodeURIComponent`/`URLSearchParams`.
- Formula injection: `text_cell` forces `data_type = "s"`; formulas enter only through `Book.formula` and the Counts/COUNTIF lines, whose criteria are escaped (`criterion`, `prefix_criterion` double quotes and escape `~ * ?`); CSV and streamed xlsx go through `safe_cell`, which prefixes `'` to `= + - @ tab CR`. `file_tabs._href` uses the shared `safe_link`.
- Config regexes (`people_rubric.yaml`, `export_rules.yaml requisition_pattern`, `platform_dictionary.yaml`, `job_lanes.yaml`) are compile-checked at load (`people/config.py:_title_rules`) and were timed against nine adversarial 30,000 character inputs (all under 50 ms, `n3.py`). Titles are capped (`max_title_chars`) before matching. Account-derived patterns use `re.escape` (`agents/entity.py:147`, `exports/readers/tech.py:45`, `jobs/match.py:22`).
- No new outbound fetch: the only provider change is two documented filter flags on the existing job feed; the people archive resolver reads the database. No SQL built from strings in new code (`.format` calls are on configuration templates; values are not re-formatted).

**Secrets and logs (item 5).** New log lines carry integers, sheet/tab keys and exception class names only, through the allow-list formatter. `EXPORT_RENDER_CHECKS` is a plain switch. `docker/*` keep `USER 10001`; `libreoffice-calc` adds package surface only. `.github/workflows/ci.yml` change is the apt line only; `infra/stack/main.tf` change is one environment variable (F4).

## Proof scripts (scratchpad)

`/tmp/claude-1000/-home-sakib-ABM-Generator/bef661d6-7235-4e44-9821-b38ffe9f59f4/scratchpad/`: `n1.py` (word-name positions), `n2.py` (URL validators), `n3.py` (config regex timings), `n4.py` (quadratic scan), `n5.py` (archive href after `scrub_rows`), `n6.py` (old vs new `client_hits`). All pure functions, no database, no network.
