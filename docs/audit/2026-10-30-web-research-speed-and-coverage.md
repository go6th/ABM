# Web research: run time and coverage (2026-10-30)

Agent E2, working tree of branch `feat/final-product` (nothing committed). The owner asked three
things: measure the run time of the web research, make it faster, and make the web search fuller,
with no leakage. No paid run, no live provider, no `gcloud`. Every figure below says whether it was
**measured** (on the hermetic fakes, latencies scaled down), **counted** (from the code or the
config), or **estimated** (arithmetic on an assumption that is named). The staging logs were not
read (not allowed); the only staging numbers used are the ones already written down in
`docs/performance/2026-10-27-speed-audit.md`, the memory notes and `docs/guide/06-known-problems.md`.

## 1. Result in twelve lines

1. The Phase 1 pull, on the fake harness with the audit's latencies (search 2.8 s, page read 5 s,
   scrape 8 s) and the governor's real slots (search 8, scrape 4), takes **1,452 s before and 783 s
   after** for the same 1,075 provider calls (caps off, as staging runs): **-46 %**. With the 60
   calls of added coverage it is 863 s, still **-41 %** against the old code. Caps on, same calls:
   1,124 s to 736 s (-35 %). (Measured, latencies scaled by 20 or 100; `scripts/bench_web_research.py pull`, section 4.)
2. The pull is now bound by the scrape provider's ceiling of 4 (`config/parallel.yaml`): 387 scrapes
   x 8 s / 4 = 774 s of the 783 s. Further speed needs the Firecrawl plan (owner decision, section 7).
3. Every grading batch re-read and re-parsed **every stored payload of the run**, one object-store
   read after another, with the parse on the event loop (job feed pages of megabytes included). A
   research grades 12 to 20 batches. Now a payload is read and parsed once per slice, side by side,
   off the loop. **Measured** (`bench_web_research.py sources`, 1,000 payloads, 15 batches, 5 ms a read, 2 ms of
   parse): 15,000 reads and 108.7 s before, 1,000 reads and 2.8 s after. At a more realistic 50 ms a
   read that is about 13 minutes of serial reads removed (estimate), and the same class of
   event-loop block that froze the worker's health check on 2026-10-28 (`06-known-problems` item 5).
4. Stored pages read back at the start of the reading step, the person-search and follow-up pages,
   the posting texts of the Jobs pipeline and the export reader's payload reads were all one after
   another. They are now 4 to 16 at a time (about 1,900 objects: 95 s to 6 s at 50 ms, estimated).
5. The entity lock reads the home page and searches the brand together (8 s instead of 11 s).
6. Fuller search: 15 more queries (76 to 91 web queries), a restricted search of SEC filings and
   the three main newswires for five rows, and the links an own-site page names (releases around a
   newsroom page, investor and leadership pages) read in full, 137 pages at most per research.
   The Data and AI lane and the two also-tracked platforms that had no vendor query now have one.
7. Added work is counted by the existing call meter, credit gate and call cap (`job_limits` now
   counts it) and priced into the estimate screen: **+20 % queries, about +300,000 input tokens and
   about +$1.50 per full research** (estimate; section 7), defaults 2.5M tokens / $11.00 to 2.8M /
   $12.50.
8. Leakage: a followed link, a restricted search and every concurrent path keep the old guards
   (own domains only, look-alike hosts, off-account pages, hostile text, tenant scope). 34 tests in
   `test_web_coverage.py` (section 6); the cache key is tenant scoped but **not account scoped** (finding L1).
9. Tests: 46 new tests. With the previous `provider.py` and `agent.py` laid over the tree, 5 of
   the 8 tests in `test_research_speed.py` fail (the three that pass pin the old pull test, the
   refusal semantics and the graph's reading of stored pages, whose code stays).
10. Not done on purpose: overlapping `pipelines` with `person_searches` or with `interpret` (source
    numbering and grading order), parallel people-list specs (a shared de-duplication order), a
    sitemap walk (needs a new connector operation), and anything that changes models or provider rates.
11. Reads of a page that the provider rate-limits are still dropped silently (`except ConnectorError`
    in the read helpers, as before): a coverage loss no row reports (finding L2).
12. Nothing was measured on a real provider: the minutes in section 3 are model results, marked as
    such; the first research on the new code should be timed with `slice.ended` / `llm.usage`.

## 2. What was read and what was assumed

Read: the memory notes for speed, slice restarts, the 2026-10-26/27 stalls, model calls dark, the
web research rebuild, the athenahealth and liveness notes, `docs/guide/02`, the orchestration
review and fix wave D1 (the ceilings, lease heartbeat and progress rules are untouched), the speed
audit of 2026-10-27, `config/parallel.yaml`, `providers.yaml`, `workflow_v1.yaml`, `models.yaml`,
`skill/pull_plan.yaml`, `skill/scope.yaml`, `platform_dictionary.yaml`, `job_lanes.yaml`,
`docs/parity/reference_spec.md` and a listing (names only, read with `python -I`) of the owner's
reference output set (`BD_phase1.zip`, `BD_workspace.zip`).

Latencies used in the models (no run was measured): web search 2.8 s (audit, p50), page read 5 s
(audit says p95 17 s; 5 s is a mean assumption), scrape 8 s (assumption: a live scrape with
`maxAge` 0), object-store read or write 50 ms (assumption), extraction call 100 s, judge call 3 s,
Opus planner call 60 to 120 s.

## 3. The timeline model of one research

A large account, caps off, as staging runs (`RESEARCH_CAPS=off`): about 1,050 provider calls in the
pull, about 2,000 stored items, about 1,000 stored payloads at grading time, about 1,900 job
postings. "Before" is the tree as it was at the start of this task (with the D1 ceilings and the
2026-10-27 fan-out); "after" is this change.

| Stage | Concurrency before | Where the time goes | Change made | Before | After | Basis |
| --- | --- | --- | --- | --- | --- | --- |
| setup | one settings read | DB | none | <1 s | <1 s | counted |
| entity_lock | 2 calls in sequence | home scrape 8 s, brand search 3 s | the two run together | 11 s | 8 s | arithmetic, test |
| cap_approval | none | DB | none | <1 s | <1 s | counted |
| pull (Phase 1) | 8 rows at a time; inside a row queries, then a search's page reads, then the own-site search and its reads, all one after another | scrape provider ceiling 4 x 8 s; searches 8 wide | queries 4 at a time, searches and reads together, stored items 4 at a time, research-owned gate per provider | 1,452 s (24 min) | 783 s (13 min); 863 s with the added coverage | **measured** (scaled bench) |
| load_manifest | none | one read | none | <1 s | <1 s | counted |
| interpret: read back | one object read after another (`_views`) | 2,000 reads x 50 ms | 16 at a time | 100 s | 6 s | estimated; test `test_stored_pages_are_read_back_side_by_side` |
| interpret: extraction | 32 calls in flight | model time: calls / 32 x ~100 s | none (models, batch size and concurrency untouched) | ~8 min | ~8 min | estimated |
| grading, every batch | **every payload of the run read and parsed again**, serially, parse on the event loop | 1,000 reads x 50 ms + parse, 12 to 20 batches | memo per payload, misses read 8 at a time, parse in a thread | 12 to 20 min | ~10 s | **measured** at 5 ms a read: 108.7 s to 2.8 s, 15,000 reads to 1,000; seconds at 50 ms estimated; test `test_a_payload_is_read_and_parsed_once...` |
| pipelines: jobs | postings 8 at a time; page reads at the governor; texts written one by one | ~18 min on a large account (staging note) | postings 16 at a time behind the scrape gate; texts 16 at a time | n/a | text writes 95 s to 6 s (estimated); the 18 min is the feed replay, unchanged | partly measured (test) |
| pipelines: tech, stack | replay + DB writes | DB | none | seconds | seconds | counted |
| pipelines: people | title specs; association lookups one technology after another | up to 10 provider jobs of up to 150 s each per lane | the lane's lookups together behind the people gate | up to 25 min per lane | ~3 min per lane at the provider's 4 slots | estimated; test |
| person_searches | up to 50 searches, 8 at a time, each with serial page reads, then put + view one by one | page reads 5 per search | the pull path above; stores 4 at a time, views 16 | ~3 min | ~1 min | estimated |
| sense_check, targeted_pulls | 1 model call; follow-up pulls 8 at a time | model | the pull path above | ~2 min | ~1.5 min | estimated |
| distill | 1 model call | model | none | 1 to 2 min | 1 to 2 min | estimated |
| build | CPU + posting texts and search payloads read one by one | 1,900 + 120 reads x 50 ms | 16 at a time | ~100 s | ~10 s | estimated; test |
| gate, deliver, finish | DB | DB | none | seconds | seconds | counted |

What the table says: on a large account the two serial costs that were not model time were the
**grading payload reload** (12 to 20 minutes) and the **pull's serial page reads** (about 11 of 24
minutes); both are gone. What is left is provider-bound (scrape slots, 13 minutes of scrapes) and
model-bound (extraction, judge, distill).

### 3.1 The in-process profile (measured)

`cProfile` of the full graph on the scratch database (`tests/system/test_abm_production.py`, the
real graph, runner, ledger, grading and tracker, scripted providers and model): 24.2 s, of which
the stage code is small. Top by cumulative time: `payloads.put` 14.0 s for 2,344 stores (6 ms
each: object write plus archive row), `call_need` 9.9 s for 5,469 calls, `build` 3.1 s, the client
scan 1.9 s for six files, grading 1.2 s for 315 batches, the tracker 0.5 s for 598 reports. YAML
parsing: 76 loads cost 2.8 s, of which 0.4 s was `grading.judge.model_profile` parsing the 375-line
`models.yaml` twice per grading batch (now cached by file state). No stage is CPU-bound in
process; the cost is waiting, and waiting is what the changes remove. The same test after the change
takes 23.6 s against 22.0 s: this harness has no latency (local database, instant providers), so
concurrency cannot help it, and the added coverage makes 31 % more stores (3,072 against 2,344). The
speed results above are therefore the scaled benches, not this profile.

## 4. Speed changes and their proof

All concurrency is bounded and ordered: results are assembled in the order the one-at-a-time code
produced them, a call that has started is never cancelled for a sibling's failure (a cancelled paid
call is paid again), and the first failure in order is raised after the rest have finished
(`crosscut/fanout.py`, `agents/production/provider.py together`).

| Change | Where | Proof |
| --- | --- | --- |
| Queries of a row asked 4 at a time; results stored in query order; a refusal stops the row where the loop stopped | `agents/pull/agent.py` (`QUERIES_IN_FLIGHT`, `STORES_IN_FLIGHT`) | `test_the_queries_of_a_row_are_asked_side_by_side...`, `test_a_refusal_stops_the_row...` (fails on the old agent) |
| Open search, own-site search and restricted search of a query together; then every page read together; feed pages while the newsroom is searched; entity lock calls together; people lookups per technology together | `agents/production/provider.py` | `test_the_page_reads_of_a_search_overlap...`, `test_the_open_search_and_the_own_site_search...`, `test_the_entity_lock_reads...` (fail on the old provider) |
| Research-owned gate per provider sized by the governor's ceiling | new `crosscut/gates.py`, used by the provider, `jobs/adapters.py`, `people/adapters.py` | `test_a_research_never_has_more_calls_at_a_provider_than_its_ceiling`, `test_page_reads_wait_at_the_research_own_gate...` |
| Row ceiling scales with the waves of queries a row has (a row is cut for a call that never answers, not for having 15 queries) | `agents/graph.py row_ceiling_s` | `test_a_row_is_given_up_for_a_call_that_never_answers...`; the D1 ceiling tests are unchanged and pass |
| Stored items read back 16 at a time and written 4 at a time (reading step, person searches, follow-up pulls, careers choice; the database pool is 10 connections for everything) | `agents/graph.py` (`VIEWS_IN_FLIGHT`, `stored_views`) | `test_stored_pages_are_read_back_side_by_side` |
| Grading sources memo, misses read 8 at a time, parse in a thread | `account_intelligence/grading/adapters.py` | `tests/grading/test_ledger_sources_memo.py` |
| `model_profile` cached by file state | `account_intelligence/grading/judge.py` | existing grading tests; profile section 3.1 |
| Jobs: 16 postings at a time, page reads and board searches behind the gate, posting texts written 16 at a time | `jobs/pipeline.py`, `jobs/adapters.py`, `jobs/stage.py` | `tests/jobs/test_adapters.py` (two new tests) |
| People: the lane's technology lookups together | `people/adapters.py` | `tests/people/test_adapters.py` (new test) |
| Export reader: search payloads and posting texts read 16 at a time | `exports/readers/pg.py` | `tests/exports/test_reader_reads_overlap.py` |

Benches (`scripts/bench_web_research.py`; the old code is read with `git show HEAD:`, so run it before
committing this change or pass `--ref <the commit before it>`). `pull` runs `PullAgent` and the real
`ConnectorProvider` over a fake `call_need` that sleeps the audit's latencies x 0.05 behind
semaphores of 8 (search) and 4 (scrape), the way the governor holds them, for the 23 web rows at 8
rows at a time; the old `provider.py` and `agent.py` are loaded from `git show HEAD:`. The same
config (91 queries) feeds both, so "old" makes the same 1,075 calls:

```
old                 caps off  1,075 calls  wall 72.6 s = 1452 s equivalent
new (speed only)    caps off  1,075 calls  wall 39.1 s =  783 s equivalent   -46 %
new (+ coverage)    caps off  1,135 calls  wall 43.2 s =  863 s equivalent   -41 % (+60 calls)
old / new caps on   653 calls              1124 s / 736 s (scale 0.01)       -35 %
```

`sources` runs the old and new `LedgerAdapter.sources` over a fake
ledger of 1,000 payloads, 5 ms a read, 2 ms of parse, 15 grading batches:

```
old: gets=15000  wall=108.72 s        new: gets=1000  wall=2.77 s
```

The ceiling matters: with the previous per-row 540 s the slowest row of the old code (15 queries in
`platform_vendor_account_team`: 1,452 s) was already past it and would have been cut and lost.
After the change the slowest rows (scale 0.01 run) are 841 s (that row) and 800 s (`company_news`, 5
queries with followed links), each given 4 and 2 waves of 540 s by `row_ceiling_s`.

## 5. Coverage review: what a research reads, and what it does not

Counted from `config/skill/pull_plan.yaml`, `provider_routes.yaml` and the code (after this change).

| Source kind | How it is reached | Depth | Gap that remains |
| --- | --- | --- | --- |
| Company site | the entity lock reads the home page; 9 own-site rows (44 queries) search the account's own domains (`include_domains`) and read every page found in full (live scrape, so a fact can be Confirmed); **new: the links those pages name are followed** (8 per newsroom query, 3 to 5 for investor, leadership, about and partner pages, one level) | a search returns at most 10 results (the API's maximum); 3 own pages read per search with the caps on, all with the caps off | no sitemap or crawl: the reference research read about 300 releases; this reaches about 40 to 140 |
| News (18-month window) | the news feed (`news_events`, 18 months, anchored to the month) + 5 newsroom queries + **new: newswire search** restricted to three hosts and to the window (`after_date`) + open-web queries on competitors, regulation, litigation | feed paged, searches 10 each | the open-web searches have no date bound, so older pages can appear |
| Press releases | own newsroom (search + followed links), feed, **newswires (new)** | as above | no archive walk |
| Filings | open queries for the 10-K / annual report / earnings; **new: a search restricted to sec.gov** for three rows (filings, financial baseline, holders), the first 2 to 3 pages scraped live (the registry's only first-tier host besides the account's) | 3 + 2 + 2 filings per query | a filing is cut at 24,000 characters (120,000 with the caps off) of its text, so a long 10-K is read in part; no EDGAR full-text or XBRL; non-US registers are not covered |
| Careers pages and job boards | a careers page chosen from the pages visited; board searches per lane keyword on that host; the 12-month feed | 3 queries per row, feed paged | other boards (LinkedIn Jobs, Indeed) are covered only through the feed |
| Vendor and partner pages | `platform_vendor_account_team` (15 queries, own-site), `vendor_customer_stories` (4), `vendor_communities` (3), `customers_partners_matched` (3, own-site) | 10 results per query | **new: the Data and AI lane (Snowflake, Azure, AWS, Google Cloud, Informatica, Palantir, OpenAI, Qlik, Looker, Alteryx, SAS, Kafka, dbt) and the also-tracked Power BI and Snowflake have vendor queries**; the other dictionary platforms (Oracle, Epic, HubSpot...) are outside the owner's fixed scope (`scope.yaml`), so they are not searched; `never_report` names (ServiceMax, Siebel, Databricks) are checked absent from every query by a test |
| Conferences and talks | `executive_speaking` (now 4 queries, one industry-neutral), `events_sponsored` (now 4, one for the account's own events), person-name searches (speaking, podcasts, video, articles, customer stories for up to 10 executives) | 10 results per search | the conference names in the query are health and platform events |
| Social | LinkedIn, X, Facebook, Instagram, TikTok are never opened (`UNREADABLE_HOSTS`): snippets only | n/a | profiles and posts have no operation (`UnavailableProfiles`, `UnavailablePosts`): `full_profiles_senior_people`, `posts_priority_people` can only be gaps |
| Review sites | `review_sites` (3 queries) | search results and the first off-site reads | G2 and similar pages are often behind bot walls |
| Tech detections | the detections feed (24 months) and the organisation tech stack | feed paged | no website technology scan |
| People | the organisation tech-stack lookups per lane (10 technologies, now together), names found in facts, the archived profile pages | 50 people per page | title search needs the BitScale grid contract (stub); internal history rows (`prior_customer_status`, `connections_alumni`) are off by decision D-98 |

Gaps against `docs/parity/reference_spec.md` and the reference zips (names only): the reference read
a newsroom walk of about 300 releases (`data/releases`), earnings transcripts, a 10-K section, the
leadership page, subsidiary domains, former-executive checks, profile findings and posts. This
change adds the link walk, the filing and newswire searches and the missing lane vendors; the sitemap
walk, profile and post reads and the subsidiary domains need new operations or an owner decision
(section 8).

Rows that cannot end Done today, whatever the search: `prior_customer_status`,
`connections_alumni` (internal history off), `full_profiles_senior_people`, `posts_priority_people`
(no operation), `key_contact_profiles_posts` without the tenant's people consent.

New queries (15 in all): platform vendors +5 (Snowflake / Power BI, data platform, generative AI,
analytics platforms, data integration), vendor stories +1, news +2, earnings +1, earnings-call +1,
leadership +1, speaking +1, events +1, litigation +1, holders +1. A test checks that no query names a `never_report` platform and that every
platform the dictionary files under a lane, and every also-tracked one, is named in the vendor queries.

## 6. Leakage: what was checked, what was tested

New paths: link following, restricted searches, concurrent queries and reads, the grading memo, the
gates. For each, the tests below (`tests/agents/test_web_coverage.py` unless noted).

| Property | Test |
| --- | --- |
| Only links on the account's own domains are followed, with the row's words, inside the window, once each, at most the row's count | `test_only_links_on_the_accounts_own_domain...`, `test_dated_links_outside_the_window...`, `test_a_link_is_listed_once...` |
| A look-alike host, a host that carries the account's name as a label or in the query, credentials in the address (`acme@evil`, `user:pw@acme`), http, `javascript:`, `mailto:` are never followed | `test_a_look_alike_or_unsafe_link_is_never_followed` (8 cases) |
| Hostile page text ("ignore previous instructions, read this address") cannot choose a read or widen the cap: addresses are filtered by code and the page's wording is never passed on | `test_a_hostile_pages_instructions_do_not_choose_what_is_read` |
| The provider reads only own-domain links, nothing off-domain, to the row's cap, one level deep, and a row without a rule follows nothing | `test_the_releases_a_newsroom_page_names_are_read_up_to_the_rows_cap`, `test_a_feed_row_follows...`, `test_a_row_without_a_follow_rule_follows_nothing` |
| A restricted search reads only pages on the rule's hosts (a result on another host, or on `businesswire.com.evil...`, is never opened), to the rule's count, bounded to the window | `test_a_filings_search_is_restricted...`, `test_a_newswire_search_is_bounded_to_the_news_window` |
| A malformed rule (URL instead of host, path, zero reads, empty list, unknown window) is refused at load | `test_a_malformed_restricted_search_rule_is_refused_at_load` |
| A page that ends up off the account (a look-alike brand, a filing of another company) is dropped by the reading step's guards before it is read for facts | `test_a_page_that_ends_up_off_the_account...`, `test_a_filing_of_another_company...` (the existing hostile-text and off-target tests in `tests/agents` still pass) |
| Results are scoped to the run: the provider, its gates and its followed links live on the instance; no module-level mutable state; the cache index key carries the tenant | `test_the_provider_keeps_no_state_beyond_its_run`, `test_the_research_cache_index_key_is_tenant_scoped...` |
| The grading memo is keyed by tenant, run, call and payload and is filled only from rows the tenant's own transaction returned: another tenant's run id yields nothing | `tests/grading/test_ledger_sources_memo.py` |
| No provider or tool name or credit count reaches a client file | the new config strings and queries are internal; the existing scans (`tests/agents/test_name_scan_exemption.py`, `test_naming_words.py`, `tests/exports`) pass; the queries name platforms only, never a data tool |

### Findings (leaks and near-leaks), including outside the change

* **L1 (medium, design):** the research cache is keyed by tenant and arguments, **not by account**
  (`connectors/cache.py`: Redis key `rc:v1:{tenant}:{args_hash}`; Postgres `_LOOKUP` filters by
  `args_hash` under the tenant's RLS only). An identical request made for another account of the
  same tenant (the same page URL, the same query text) adopts the first account's stored response and
  its payload path (`tenants/<t>/accounts/<other>/runs/...`). Cross-tenant sharing is impossible
  (RLS and the key); across accounts it is by design ("never pay twice") and is open question 5 of
  the leakage review. Two consequences to decide: the second account's replay depends on the first
  account's objects (an account delete would break it), and cost and timing reveal that a colleague
  researched the same page. I did **not** change this (the brief says to keep cache semantics).
  Scoping the lookup to the account is one extra join on `account_runs` and loses only the savings
  between accounts that share a page or a domain.
* **L2 (low):** the page read helpers swallow `ConnectorError`, which includes `RateLimited` and
  `CallInFlight`: a page the provider rate-limits is dropped with no gap and no log. Not a leak, a
  silent coverage loss. A bounded local retry (or a `reads_dropped` counter) would show it.
* **L3 (info):** a hostile page can name any address on the account's own domains; those pages are
  read and then pass the same drops as any own page. A redirect from an own address to another host
  ends in the connector's SSRF checks and then the reading step's off-account filter (tested).

## 7. Cost and owner decisions

Counted from the config (`pull_plan.yaml`), worst case per research: followed pages 137 live
scrapes; restricted searches 20 searches + 49 reads; 15 more queries each with a search, an own-site
search and up to 3 + 2 page reads. Estimated cost: about +300,000 input tokens (Opus input $4 per
million: +$1.20), about +200 page reads and 20 more searches at the providers (search $0.005 each:
+$0.10; scrape 1 credit a page, price per credit unknown in `providers.yaml`: +$0.20 at $0.001).
**About +$1.50 and +12 % tokens per full research.** `config/usage_estimate.yaml` defaults for
`full`, `new_research` and `file_public` went from 2.5M tokens / $11.00 to 2.8M / $12.50 (they are
replaced by the workspace's own median after its first runs). The tool call cap of a job with the
caps on (`replica/jobs.py job_limits`) now counts the real queries, the own-site searches, the
followed pages and the restricted searches. `replica/credits.py` was not touched; its credit
price for a full research (220 credits = $11.00, also `max_budget_usd`) is now below the estimate:
**with the caps on, a large research can reach its budget before it finishes. The owner should
decide whether `full` is raised in `config/credits.yaml` (E1 owns the reserve floor in
`credits.py`).**

Owner decisions:

1. **Firecrawl plan.** `p_scrape` runs at 4 in flight and 1 a second (`parallel.yaml`,
   `providers.yaml`: a placeholder, "plan-dependent"). The pull is bound by it. Hobby allows 5
   concurrent browsers, Standard 25. Knowing the plan, raising `ceilings.p_scrape` would cut the
   783 s of the pull: 387 scrapes x 8 s / 12 slots is 260 s, but at the current pace of one a
   second 387 scrapes still take 387 s, so `rate_per_s` must rise with it. Not changed: not a
   documented allowance.
2. **Cache scope** (L1): keep tenant-wide sharing or scope it to the account.
3. **Credits for a full research** (above).
4. **Sitemap walk.** To reach the reference's 300 releases the pull needs the site's sitemap or the
   provider's map operation (same vendor, a new connector operation with its cost model).
5. Whether the conference names in `executive_speaking` should depend on the account's industry.

## 8. Not done, and why

* **Overlap of `pipelines` with `person_searches` or `interpret`.** The code pipelines allocate
  source codes (S-0001...) in the order they register sources and grade through one engine path that
  pins the run's config per batch (the reading step serialises it with a lock). Running them side
  by side would make the numbering and the lead states depend on timing. A safe version is a
  *prefetch* (run only the jobs pipeline's fetches while the model reads, so the pipeline replays
  them); it needs the careers host before the model chooses it. Estimated gain on a large account:
  most of the jobs pipeline's fetch time. Left for a decision.
* **People specs in parallel.** `people/pipeline.py run_pass` shares `seen_people` and the
  per-run cap across specs; the result depends on the order. The active source offers association
  searches only, a handful per run, so the gain is small.
* **Bigger extraction batches.** `MAX_CALL_CHARS` (120,000) is bounded by the model's output
  limit, not by the pipeline; changing it changes the model's work. Models, effort and concurrency
  (`EXTRACT_CONCURRENCY` 32) are untouched, as asked.
* **Poll intervals.** In `fast` mode no research call is batched, so `batch.poll_seconds` is not on
  the path; the provider-job poll (3 s) is the provider's documented 2 to 5 s.
* **Ledger "started" attempts after a ceiling** (D1 section 5): with more calls in flight, a row cut
  by its ceiling leaves more `started` attempts that an identical call in the same slice waits for.
  The fix changes the paid-call ledger and stays an owner decision.
* **The account-scoped cache, the reads' silent drops, a sitemap operation, profile and post
  operations**: sections 6 and 7.
* **Memory.** The grading memo keeps the parsed text of a run's sources in the worker's memory for
  the length of one slice: tens to a few hundred MB for a large account, on a 16 GiB worker with
  concurrency 4. It is bounded by the run, not by time; if a worker is ever short of memory the
  memo is the first thing to bound.
* **Anything on the files E1 owns.** `credits.py`, `auto_resume.py`, `workspace.py` STOP_TEXT, the
  infra stack: not touched.

## 9. Commands and results

All from `/home/sakib/ABM Generator`, own scratch database `ai_test_e2`, `-m "not eval and not live"`,
one pytest session at a time, no `.env` sourcing (`APP_RW_PASSWORD` by `grep`).

```
TEST_DB=ai_test_e2 PGPASSWORD=dev APP_RW_PASSWORD=... \
  MIGRATOR_DATABASE_URL=postgresql://postgres:dev@localhost:5432/ai_test_e2 \
  .venv/bin/pytest <dirs> -m "not eval and not live" -q -p no:cacheprovider
```

| Session | Directories | Result |
| --- | --- | --- |
| 1 | `tests/connectors parallel workflow runs llm replica system crosscut unit exports grading tracker` | 2621 passed, 1 failed: `tests/system/test_static_guards.py::test_interrogate_docstring_coverage_is_complete` (six nested functions without a docstring: mine and two older ones in `archive.py` and `graph.py`; docstrings added, interrogate 100 %) |
| 2 | `tests/agents jobs people crosscut grading exports system replica unit tracker` | 3128 passed, 1 xfailed, 3 failed: two call-order tests in `tests/agents/test_research_core.py` (an own-site row now also asks the register or the newswires; updated) and `gen_loops.py --check` (line numbers; regenerated) |
| 3 | `tests/agents crosscut jobs people grading replica unit`, `tests/system/test_abm_production.py`, `test_static_guards.py`, `tests/exports/test_reader_reads_overlap.py` (after the last edits) | **1917 passed, 1 xfailed, 0 failed** |

Sessions 1 and 2 loaded the source before the last few edits (the stores-in-flight value, six
docstrings, the loops file); session 3 ran after them. `tests/connectors`, `parallel`, `workflow`,
`runs`, `llm` were not run again after session 1: nothing they import changed afterwards. The whole
suite was not run (the owner runs it); `tests/e2e`, `tests/isolation`, `tests/api`, `tests/worker`
and `web/` were not run.

Static checks (all pass at the end): `ruff check src tests`, `ruff format --check src tests`
(1,237 files), `mypy src` (705 files), `lint-imports` (19 contracts kept), `interrogate src` (100 %),
`scripts/gen_loops.py --check`, `scripts/wiring_audit.py --check`, `scripts/check_route_catalog.py`,
`scripts/check_guarantees.py`, `scripts/check_comments.py`.

Reproduce the benches: `python scripts/bench_web_research.py pull old|new 0.05 caps_off [nofollow]`
(`nofollow` turns the new coverage rows off) and `python scripts/bench_web_research.py sources
old|new 1000 15 5`.

## 10. Files changed

Source: `src/agents/production/{provider,links}.py` (new `links.py`), `src/agents/pull/agent.py`,
`src/agents/graph.py`, `src/agents/config.py`, `src/crosscut/gates.py` (new), `src/jobs/{adapters,
pipeline,stage}.py`, `src/people/adapters.py`, `src/account_intelligence/grading/{adapters,judge}.py`,
`src/exports/readers/pg.py`, `src/account_intelligence/replica/jobs.py` (`job_limits` only), `scripts/bench_web_research.py` (new);
three docstrings added in `src/account_intelligence/archive.py` and `graph.py` for the docstring guard.
Config: `config/skill/pull_plan.yaml` (queries, `follow`, `also_on`), `config/usage_estimate.yaml`.
Tests: new `tests/agents/test_web_coverage.py`, `tests/grading/test_ledger_sources_memo.py`,
`tests/exports/test_reader_reads_overlap.py`; extended `tests/agents/test_research_speed.py`,
`tests/agents/test_research_core.py` (call order of an own-site row), `tests/jobs/test_adapters.py`,
`tests/people/test_adapters.py`. Docs: `docs/guide/02`, `04`, `06`, `docs/architecture/loops.md`
(regenerated), this file.
