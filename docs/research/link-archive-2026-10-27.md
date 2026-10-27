# Link archive and the LinkedIn profile links, 2026-10-27

Why People rows reached the files with a position but no profile link, why links that were found
went stale, what the reference deliverables (the owner's BD zip) carry, and the archive that
keeps every stored address with the record it came from. Counts in part 2 are measured on the
reference files with `python -I` over the extracted zip; the rest is read from the code.

## 1. Where a link is captured and where it was lost

| Link kind | Where it is captured | Where it was lost | Fix (this change) |
| --- | --- | --- | --- |
| LinkedIn profile of a person the people source returned | `connectors/providers/p_org.py` `PersonRecord.profile_url` (the record's `linkedin_url` attribute, requested in `config/providers.yaml people_attributes`); `people/adapters._people_page` carries it as `RawPerson.profile_url` | A record whose attributes came back without `linkedin_url` has `profile_url None`; `people/scope.classify` ends it `unresolved` ("no profile link yet") and `people/export.build_export` never lists it: the person vanished from the People List with the position known | `people/mentions.resolve_unlinked` (run in `PeoplePipeline.run_pass` before the merge): one free query per unlinked record to the resolver; the profile page the research's own searches archived for that name is matched by the whole normalised name (`people/archive_links`), the record keeps everything and gains the link, merges by URL and is listed |
| LinkedIn profile pages the Phase 1 key-contact searches returned (`key_contact_profiles_posts`: "{account} chief information officer OR CIO" ...) | `agents/production/provider.items_of`: one `SourceItem(url, text)` per search result; `agents/production/payloads.StorePayloads.put` stores it | Read only by the pass 1 extractor, which proposes `executive_role` facts; the profile URL reached a fact only when the extractor quoted that very snippet. The people pipeline never saw these pages (`NoMentions` resolver in production): the richest source of profile links for named executives was never used for the People List | `StorePayloads` records every public item in the link archive (`kind` profile for a person's own page); `people/adapters.ArchiveResolver` is the production `MentionResolverPort` over those archived pages |
| People named in sources (an executive quoted in a release) | `agents/extraction` `executive_role` facts with `fact_evidence.url` = the page that named them | `agents/production/pipelines.run_people` wired `NoMentions`: the people pipeline's mention loop had nothing to resolve, so a named executive without a search-returned profile never became a People row | `people/adapters.FactMentions`: every public, current `executive_role` fact of the account becomes a `Mention` (name, page, sentence); resolved through the archive resolver; listed with found-by "Research (named in a source)" |
| Profile on the web People sheet's fact rows (before a delivery) | `replica/sheets.py SHEETS["people"]`: `value_json link`, `profile_url`, the newest evidence URL when it is a LinkedIn profile, or a `people` row of the same name | The lateral took ONE evidence row (newest `evidence_date`): a fact quoted from a release (dated) and from the profile (undated) lost the profile | the profile expression takes any evidence of the fact that is a profile page |
| Job posting link | `jobs/pipeline` posting `url` and `text_ref` (the posting text stored in the payload store) | Never lost; it dies when the requisition closes (the careers site removes closed postings) | archived in `jobs/repo.save_run` with `format text`: the stored posting text is the copy |
| Fact source page (release, filing, news page) | `fact_evidence.url` with `source_ref = <ledger response key>#<record index>` (the raw provider body) | The raw body is a provider payload and names the provider: it could never be shown to a client, so a dead page had no copy to open | the same URL was a Phase 1 item, archived by `StorePayloads` with its text (the record text the extractor read) |
| Technology detection evidence page | `agents/production/pipelines.write_detections` `source_ref` (registered source) | No copy at all | a small JSON body (technology, first and last seen, source count) archived under the page URL |
| Internal history (mail, drive, notes) | private payload items | Correct: never archived for the client screens (`visibility private` skipped) | unchanged |

The connector ledger (`tool_calls.response_ref`) already kept every raw body; what was missing was
an index from an address to a copy that can be served, with the time it was captured.

## 2. The reference deliverables (the standard)

Extracted from the owner's zip into an empty scratch folder, counted with `openpyxl` and the
docx relationship files. Each table is one workbook sheet; "with link" counts cells that carry a
hyperlink or an http(s) address.

| File | Sheet or part | Rows | Link column | With link | Without | Profile links | Other |
| --- | --- | --- | --- | --- | --- | --- | --- |
| People List | People | 736 | LinkedIn | 735 | 1 | 735 | 0 |
| People List | Summary | 11 | none | | | | |
| Job Postings | Jobs | 1,897 | Link | 1,897 | 0 | 0 | 1,897 (129 on the LinkedIn jobs host) |
| Job Postings | Summary | 7 | none | | | | |
| Tech Stack and Teams | Platform Ranking, Confirmed stack (212), Platform teams (25) | | none | | | | |
| Account Summary (docx) | whole file, 14 tables | | 111 external links | | | 49 profile links (the Key people table) | 62 |
| Sources and Evidence (docx) | whole file, 14 tables | | 299 external links | | | 7 profile, 11 other LinkedIn | 281 |
| Lane Highlights (docx) | | | 0 links (by design) | | | | |
| phase1 zip, workspace zip | | | 332 and 372 entries: the raw pulls, one text file per page read (`data/releases`), the build scripts | | | | the archive the reference keeps by hand |

The standard the product must meet: every People row has a profile link (735 of 736 in the
reference; the one row without a link is a named executive), every posting row has its link,
the Key people table links the profile of every person it names, and every page read is kept
as text beside the deliverables.

The product's sheet builders (`config/export_rules.yaml workbooks`, `exports/readers`): the
People workbook has the same 13 columns plus Ref and Source code, with LinkedIn as a link
column ("Open profile"); Jobs has the reference's 14 columns in the reference order (Last seen
and Seniority are not stored, so not printed) plus Found by, Ref and Source code; Tech has the
reference's three sheets. Before this change the People workbook listed only people with a
profile link (`people.export`) and reported the rest as "no profile link yet" on the Summary
sheet, so its link coverage was 100 percent of fewer people; the fix above lists the people the
archive resolves. The web People sheet before a delivery (the live sheet over facts) showed the
page that named a person as the row's link and a profile only when it was the newest evidence.

## 3. The archive

* `link_archive` (migration 0086): one row per captured copy of an address: original URL,
  normalised URL and its SHA-256 (the lookup key, proven by a CHECK), kind (profile, posting,
  page, feed_item, detection), the neutral payload key of the copy, its format (a JSON item with
  `url` and `text`, or the stored posting text), a title, `archived_at`, and the ledger reference
  of the raw body for traceability (never served). Insert-only; the newest copy of an address
  wins. Owned like the account (private workspaces): the writer names the account's owner.
* Writers: `StorePayloads.put` (every public Phase 1 item, person-name search and targeted pull
  item), `people/repo.save_run` (a small JSON record per profile link), `jobs/repo.save_run`
  (every posting with stored text), `pipelines.write_detections` (the detection's page).
* Readers: `archive.lookup` gives the sheets the copy behind each link of a page of rows
  (`sheets.attach_archives`: the row's `archives` map, column key or `link`), the web table shows
  an "archived" link beside each source link (`SheetView.tsx`), and
  `GET /v1/accounts/{domain}/archive?url=...` serves the newest copy as a readable page (JSON when
  asked for), 404 outside the tenant; `archive.profile_links` feeds the people resolver.
* Not changed: the delivered workbooks keep the reference's columns (no archive column); the
  Sources and Evidence Part G still tells the reader to ask for the saved copy. The archive copy
  for a Phase 1 search result is the snippet the search returned when the page itself was not
  read; a page read in full is archived whole.

## 4. Tests

`tests/replica/test_link_archive.py` (record, newest wins, attach, the route and tenant
isolation), `tests/people/test_archive_links.py` (title parsing, candidates, unlinked
resolution, the pipeline linking the corpus's unlinked record), the people and jobs repository
suites (archive rows per profile link and per posting), `tests/agents/test_production_adapters.py`
(public items archived, private not), `web/tests/unit/sheet-view.test.tsx` (the archived link).
The synthetic people corpus gained one record merged by name (the reference behaviour the
golden oracle keeps testing once the unlinked record is linked by URL).
