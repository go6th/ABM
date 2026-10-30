# Archive records: one clean, structured record per page (2026-10-31)

Owner brief: every page the research reads is kept as one clean, structured record: the article
only (no menus, footers, badges, repeats), tables rebuilt as tables, the source classified and
graded, typed facts pulled out, commentary kept apart from facts, and the archive page and index
showing it. Nothing is committed.

## What a record is now
`page-archive/2` and `news-archive/2` (version 1 stays readable). Fields: `title, publisher,
author, published_date, collected_at, language, source_type, is_primary, is_ai_generated,
derived_from, fact_grade, summary, body_markdown, tables[{title,columns,rows}], events, people,
organizations, figures, quotes, analysis[{author_type,text}], disclaimer, tags, lanes,
duplicate_of, corroborates, content_hash, quality`. The download adds `account_domain` and
`record_id` (`arc_<domain>_<12 hex of content_hash>`; a hash, not a counter). The article is stored
once (`body_markdown`); there is no second `text`/`article_text`.

| Step of the brief | Where | Notes |
| --- | --- | --- |
| 1 Clean | `account_intelligence/archive_clean.py`, rules in `config/archive_clean.yaml` | chrome lines, menu runs (3+ short lines), text before the headline, footer from the first marker once 30 words are kept, links reduced to text, empty links and images dropped, escapes undone, bold labels become `##` headings, repeats (>= 30 words, >= 80% contained in what was kept) removed, flattened and multi-line pipe tables rebuilt, disclaimer and commentary split out, byline taken out, language by stop-word vote. Per-publisher chrome under `publishers:` in the YAML |
| 2 Classify | `archive_facts.classify` | host lists in the YAML. AI marker => `AI-generated summary`, never `Confirmed`. `is_primary` only for the account's own site, or a wire release / filing that names the account. `derived_from` names the wire and takes its address from the page's own links, else `Not found` |
| 3 Facts | `archive_facts` | appointments, retirements/departures with dates, other event types from the headline and lead, organisations with ticker and role, money/percent figures beside a metric word, verbatim quotes with speaker. A name must pass `person_name`; `previous_role` only when the page says it, else `Not stated` |
| 4 Store | `archive_records.web_record`, writer `agents/production/payloads.py` | every text passes contact redaction and the output guard; the article is guarded paragraph by paragraph (a refused paragraph goes, the rest stays; `withheld` names the field). The copy row's `source_ref` now holds the reference of the pull's own payload (the page as pulled), for audit, never served |
| 5 View | `web/components/accounts/ArchiveView.tsx`, `ui/components.css` | header badges (source type, grade), Open original / Open primary source / Download JSON, key-facts chips, summary, people table, source tables, article text (collapsed over 300 words), commentary box "Third-party / AI commentary — not a fact", disclaimer, provenance footer. Index: Title, Published, Publisher, Source type, Grade, Event types, People named, Lane, Collected, Links; a GET filter form with one box per column |
| 6 Link records | `archive_reconcile.py` | same person + same kind of change + effective dates within 3 days = one event. Master = primary, then grade, then non-AI, then earlier. Others get `duplicate_of` (>= 80% same words) or `corroborates`; an AI/aggregator record gets the master's address in `derived_from` when the master is the primary source |
| 7 Validate | `archive_records.validate_web_record`, `archive_clean.check_clean` | flags, not rejects: a copy that fails is still kept (its job is to survive a dead link) with `quality.validation = "flagged"` and `quality.flags` |
| 8 Backfill | `archive_backfill.py`, `scripts/archive_backfill.py` | below |

API: `GET /v1/accounts/{domain}/archive/list` takes `title, publisher, published, source_type,
grade, event_type, person, lane, collected` (case-insensitive "contains" on the field stored beside
the row, `link_archive.meta`; wildcards in the value are text). `ArchiveListItem` carries the new
columns. OpenAPI and the generated client are regenerated.

## Backfill
```
python scripts/archive_backfill.py [--tenant UUID] [--account DOMAIN] [--apply] [--force]
```
Dry run unless `--apply`; the report is `Logs/archive_backfill_report.md` either way (records
processed, boilerplate removed, duplicates merged, tables repaired, AI summaries flagged, primary
sources matched, links, validation failures with reasons). It reads the stored page text and never
fetches or calls a provider or model. The new copy keeps the original capture time (the table is
insert-only; the equal time with a higher id makes the new copy the newest).

## What is not done, and why
* **Raw HTML.** The research stores the page text, not raw HTML, so there is no `raw_html`
  attachment and no "View raw HTML" link; the pulled payload stays in the payload store and its
  reference is on the copy row from now on. Old copies were rebuilt from their stored text.
* **Re-fetch.** A stored copy with no text is listed in the report under "needs re-fetch" and left
  as it is. Fetching is a paid call; it needs a go-ahead.
* **Finding a missing original by search.** Not done (paid). `derived_from.url` is filled only from
  a link on the page itself or from another record of the same account (step 6).
* **Step 6 at write time.** Linking runs in the backfill. A research that re-reads a page writes a
  fresh record without links; call the backfill after a run to relink, or wire `reconcile` to the
  run end (not done).
* **Rule-based extraction.** Names, roles and events come from sentence patterns, not a model:
  conservative (it misses phrasings), never inventive. Add a phrasing in `archive_facts.py`; add a
  host or a chrome line in the YAML.
* **Not looked at in a browser.** The viewer is covered by unit tests (20 in
  `web/tests/unit/archive-pages.test.tsx`) and a type check; no screenshot was taken.
* The index keeps Title as the first column (an existing test: a phone that scrolls the table
  sideways keeps the link) with Published second; the brief lists Published first.

## Tests
`tests/replica/test_archive_clean.py` (14, pure: cleaning, tables, dedupe, classification, facts,
guard, index fields, linking), `tests/replica/test_archive_backfill_db.py` (dry run, apply, capture
time kept, linking, no-text copy left alone, second run writes nothing, every filter, download
fields), web unit tests, `tests/agents/test_production_adapters.py` updated for version 2.
