# ADR-0011: evidence dating and read origin (D-14)

- Status: Proposed (needs product owner)
- Date: 2026-10-02
- Deciders: TODO (product owner)

## Context
The reference engine measures age from `retrieved_at`, so a three-year-old article fetched today
scores as fresh (finding F3). G5 needs to know whether a text was a live full-page read this run.

## Decision (proposal, `config/grading_policy.yaml` `dating`, `origin_by_operation`)
* Live-state pages (account domain, verified careers host, registry entries with `live_state`) are
  dated by retrieval (`observed`). Dated documents use their published date (`published`). A dated
  document without one is `unknown`: age from retrieval, flagged `date_unknown`, and it cannot carry
  engine grade A (capped at B).
* Origin by connector operation, from the providers' documents:
  * `scrape_job_page`: `full_page_read`. The connector now sends `maxAge: 0`; the documented default
    (2 days) could return a cached copy. Claim C12, [VERIFY] by a live smoke test.
  * `read_page`: `cached_copy`. The extract fetch policy's minimum live-fetch age is 600 s and the
    default is a dynamic cache policy (claim S15), so a read cannot be proven to be from this run.
  * search: `search_snippet`; feeds: `feed_item`; broker data: `broker_record`.

## Consequences
* Today only job pages read through p_scrape can be Confirmed. Company-site reads (leadership,
  newsroom) cannot, because p_scrape is limited to job pages by the adapter rule and p_search reads
  may be cached. Decide one of: allow plain p_scrape on the account's own pages; accept a 600 s cache
  window for p_search with `disable_cache_fallback`; or keep the restriction.
