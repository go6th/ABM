# Provider connectors: internal documentation

INTERNAL ONLY. Vendor names appear in this folder, in `config/providers.yaml` display
fields and in `src/account_intelligence/connectors/registry_internal.py`. Nowhere else.

| Code | Spec | Operations | Status |
|---|---|---|---|
| p_search | [p_search.md](p_search.md) | web_search, read_page | Done |
| p_scrape | [p_scrape.md](p_scrape.md) | scrape_job_page (plain) | Done; JSON and search-with-scrape refused |
| p_signals | [p_signals.md](p_signals.md) | news_events, job_openings, tech_detections | Done |
| p_org | [p_org.md](p_org.md) | org_resolve, org_tech_stack, people_by_technology | Done; org_teams not yet specified |
| p_people | [p_people.md](p_people.md) | people_by_title, profile_enrichment | Blocked: needs the workspace grid contract |

* `sources/`: first-party documents as retrieved (2026-10-02), with `INDEX.md` hashes.
* `contract/cassettes/`: response bodies copied verbatim from those documents
  (`tests/connectors/build_cassettes.py` rebuilds them).
* `contract/golden/`: expected normalised records for each cassette (contract tests).
* `CLAIMS.md`: every provider fact used, with its citation; `[VERIFY]` list.
* `samples/`: put supplied OpenAPI files or real sample responses here.

## Open items for the product owner

1. **Built-in fallbacks.** The playbook routes `open_web_search` and `read_known_page`
   fall back to `web_search_builtin` and `fetch_builtin`. Those are the chat skill's
   built-in tools and do not exist in the service. They are treated as unavailable, so
   when p_search is down these needs are **Blocked with a reason**. Decide whether to
   accept that, or name a second provider for these needs.
2. **Fallbacks that need planning.** `news_tech_job_feeds -> p_search` and
   `people_by_title <-> people_by_technology` cannot be translated mechanically (a feed
   is not a search query; a title is not a technology). They return Blocked until the
   planner issues the fallback call itself.
3. **p_people contract** (see p_people.md "What to supply").
4. **Dollar prices per credit or request** for p_scrape, p_signals, p_org (and the
   p_people cost per run). Until set, each such call charges the run budget the
   configured `unknown_cost_reserve_usd` (0.25) and is flagged `credits_only`/`unknown`.
5. **Plan rate limits** for p_scrape and p_signals (conservative placeholders now).
