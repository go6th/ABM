# Provider claim ledger (INTERNAL)

Every provider-specific fact the code or config relies on, with its citation. All
sources were retrieved on **2026-10-02** by `curl` from first-party documentation; the
files and SHA-256 hashes are in `sources/INDEX.md`. A row without a citation is a
defect; unsourced facts are tagged **[VERIFY]**, have a `# VERIFY(<code>)` comment where
code depends on them, and a skipped test in `tests/connectors/test_verify.py`.

Paths below are relative to `docs/providers/sources/`.

## p_search

| # | Claim used | Where used | Citation |
|---|---|---|---|
| S1 | Base URL `https://api.parallel.ai` | config `base_url` | p_search/search.openapi.md `servers` |
| S2 | Header `x-api-key` | config `auth` | p_search/search.openapi.md `securitySchemes.ApiKeyAuth` |
| S3 | `POST /v1/search`; request `search_queries`, `objective`, `mode`, `advanced_settings.max_results`, `source_policy.include_domains`, `after_date` | `providers/p_search.py` build_request | p_search/search.openapi.md `V1SearchRequest`, `AdvancedSearchSettings`, `SourcePolicy` |
| S4 | `mode` values `turbo`, `fast`, `basic`, `advanced`; default `advanced` | `models.WebSearchArgs` | same, `V1SearchRequest.mode` |
| S5 | `max_results` default 10 | `models.WebSearchArgs` (cap 10) | same, `AdvancedSearchSettings.max_results` |
| S6 | Response `search_id`, `results[]{url, excerpts}` required; `title`, `publish_date` optional | `_SearchResponse` | same, `V1SearchResponse`, `V1WebSearchResult` |
| S7 | `POST /v1/extract`; `urls` (up to 20), `objective`, `advanced_settings.full_content` | build_request | p_search/extract.openapi.md `V1ExtractRequest`, `AdvancedExtractSettings` |
| S8 | Extract response `extract_id`, `results`, `errors` required; result `url`, `excerpts` required; error `url`, `error_type`, `http_status_code` | `_ExtractResponse` | same, `V1ExtractResponse`, `V1ExtractResult`, `ExtractError` |
| S9 | 401, 402, 403, 404, 422 not retryable; 408, 429, 500, 502, 503 retryable | `classify_error` | p_search/errors.md "Error Reference" |
| S10 | 600 requests per minute for Search and for Extract | config `rate_limit` (10/s) | p_search/rate-limits.md |
| S11 | Search $0.001 (`turbo`/`fast`) or $0.005 (`basic`/`advanced`) per request incl. 10 results; +$0.001 per additional result | config `usd_by_mode` | p_search/pricing.md "Search API" |
| S12 | Extract $0.001 per URL | config `usd` | p_search/pricing.md "Extract API" |
| S13 | **[VERIFY]** Retry-After on 429 | default cooldown | not documented |
| S14 | **[VERIFY]** failed Search/Extract calls billed or not | `bill_on_failure: false` | not documented |

## p_scrape

| # | Claim used | Where used | Citation |
|---|---|---|---|
| C1 | Server `https://api.firecrawl.dev/v2`, path `/scrape` | config + build_request (`/v2/scrape`) | p_scrape/v2-openapi.scrape-extract.json `servers`, `paths./scrape` |
| C2 | Bearer auth | config | same, `securitySchemes.bearerAuth` |
| C3 | Body `url` required; `formats` items are objects with `type: markdown` | build_request | same, `/scrape` requestBody, `Formats` |
| C4 | `timeout` default 60000 ms, max 300000 ms | config `read_s: 75` | same, `ScrapeOptions.timeout` |
| C5 | Response `success`, `data.markdown`, `data.metadata.sourceURL`, `.url` (final after redirects), `.statusCode`, `.title` (string or list) | `_ScrapeResponse` | same, `ScrapeResponse`; p_scrape/scrape-feature.md example |
| C6 | Statuses 400, 402, 403, 404, 409, 429, 500, 503 | `classify_error` | same, `/scrape` responses |
| C7 | 1 credit per scraped page; JSON format +4; search 2 per 10 results + per-page scrape | config `credits`, `refused_modes` | p_scrape/billing.md "Credit costs" |
| C8 | Returned document is billed even for target 403/404; no document is 0 credits | `cost.actual` (`credits_per_document`) | p_scrape/billing.md "When credits are charged" |
| C9 | **[VERIFY]** plan rate limit | config placeholder 1/s | p_scrape/rate-limits.md (plan tables) |
| C10 | **[VERIFY]** `x-request-id` idempotency for plain scrapes | not used | documented only for `alexandria` |
| C11 | **[VERIFY]** dollars per credit | `usd_per_credit: null` | plan-dependent |

## p_signals

| # | Claim used | Where used | Citation |
|---|---|---|---|
| G1 | Server `https://predictleads.com/api/v3` | config | p_signals extract `servers` |
| G2 | Headers `X-API-KEY` + `X-API-TOKEN` | config `auth` | same, `security`, `securitySchemes` |
| G3 | Paths `/companies/{company_id_or_domain}/news_events`, `/job_openings`, `/technology_detections` | build_request | same, `paths` |
| G4 | `page` (default 1), `limit` (default 100, max 1000); `meta.count` only when `page` is sent | build_request, parse, `SignalsConnector` limit check | same, parameters; `info_description` "Pagination" |
| G5 | Delta filters `found_at_from` (news), `first_seen_at_from` (jobs, tech) | `_SINCE_PARAM` | same, parameters |
| G6 | JSON:API envelope `data`, `included`, `meta` | `_Envelope` | same, dataset schemas |
| G7 | News attributes and `most_relevant_source` relationship to an included article (`url`, `title`, `published_at`) | `_NewsItem`, parse | same, `NewsEvent`, `NewsArticleLite` |
| G8 | Job attributes `title`, `normalized_title`, `description`, `url`, `first_seen_at`, `last_seen_at`, `posted_at`, `status`, `seniority`, `location`, `categories` | `_JobAttrs` | same, `JobOpening` |
| G9 | Tech attributes and relationships `technology`, `seen_on_subpages`, `seen_on_job_openings` | `_TechItem`, parse | same, `TechnologyDetection` and included types |
| G10 | 401 credentials; 402 monthly request limit; **403 rate over limit**; 404; 422; 429 with `Retry-After` seconds; 500; 503 | `classify_error` | same, responses |
| G11 | Monthly request quota by plan; each page is one request | config `requests_per_page` | same, `info_description` "Request Limits" |
| G12 | **[VERIFY]** numeric rate limit | config placeholder 1/s | not published |
| G13 | **[VERIFY]** dollars per request; failure billing | `usd_per_credit: null`, `bill_on_failure: false` | plan-dependent / not stated |

## p_org

| # | Claim used | Where used | Citation |
|---|---|---|---|
| O1 | Base `https://api.sumble.com`, paths under `/v9/` | config, build_request | p_org/api-overview.md "Base URL"; extract `servers` |
| O2 | Bearer auth | config | p_org/api-overview.md "Authenticate" |
| O3 | `POST /v9/organizations` with `organizations[{url}]`, `select.attributes`; one row per input echoing `input`; unmatched row has empty `attributes` | build_request, parse | extract `EnrichOrganizationsRequest`, 200 example |
| O4 | Free attributes `id`, `name`, `slug`, `url`, page URL field; 1 credit base + 1 per paid attribute | config `org_free_attributes`, cost estimate | p_org/organizations.md "Credit cost" |
| O5 | Provider page-URL response field name | config `page_url_field` | extract `Attributes`, `PersonRow` |
| O6 | `GET /v9/organizations/{organization_id}/techs`; `business_functions[].categories[].technologies[]{name, slug, job_post_used_count}`; 1 credit per technology returned | build_request, `_TechResponse`, config | extract path + example; p_org/api-overview.md "Credits" |
| O7 | `POST /v9/people` filter mode: `organization_ids`, `query.query`, `select.attributes`, `limit` (max 200), `offset` | build_request; `OrgConnector` limit check | p_org/people.md "Input"; extract `EnrichPeopleRequest` |
| O8 | Query `technology EQ '<slug>'` | build_request | extract `PersonAttributes.technologies` description |
| O9 | Async: start returns `request_id` + `status: pending`; poll with `{request_id}` alone; statuses pending/running/succeeded/failed | parse, `poll_request` | p_org/people.md "Asynchronous requests" |
| O10 | Charged once on first `succeeded` poll; polling free; failed costs nothing | ledger `submitted` resume; cost model | p_org/people.md "When you're charged" |
| O11 | Stop when `offset + limit >= total` | parse next cursor | p_org/people.md "Search" |
| O12 | `name` free; 1 credit per person + 1 per paid attribute | config `people_free_attributes`, estimate | p_org/people.md "Credit cost" |
| O13 | `file_name` returns a hosted file | never sent; `file` refused | p_org/api-overview.md "File downloads" |
| O14 | 10 requests per second per user | config `rate_limit` | p_org/api-overview.md "Rate limits" |
| O15 | 55 s server limit, 504 not charged; client timeout 65 s or more | config `read_s: 70`, `total_s: 75` | p_org/api-overview.md "Timeouts" |
| O16 | 202, 400, 401, 402, 429, 500, 504 | `classify_error` | p_org/api-overview.md "Error responses" |
| O17 | `credits_used` in responses | `ParsedPage.provider_credits` | extract response schemas |
| O18 | **[VERIFY]** Retry-After on 429 | default cooldown | not documented |
| O19 | **[VERIFY]** dollars per credit | `usd_per_credit: null` | plan-dependent |

## p_people

| # | Claim used | Where used | Citation |
|---|---|---|---|
| P1 | Base `https://api.bitscale.ai/api/v1` | config | p_people/api-reference.md header |
| P2 | Header `X-API-KEY` | config | same, section 2 |
| P3 | 5 requests per second per workspace | config `rate_limit` | same, section 3 |
| P4 | 401, 403, 404, 429, 500 codes | `classify_error` | same, section 7 |
| P5 | **[VERIFY]** grid output contract for people and profile operations | operations raise `NotSupportedYet` | workspace-defined; see p_people.md "What to supply" |
| P6 | **[VERIFY]** cost per run | `cost_model: unknown` | not in the API reference |

## PRD playbook hints kept as hints

The playbook cost hints are stored as `prd_hint` in `config/providers.yaml`, each marked
"source: PRD tools playbook, verify against current plan". None of them drives a number
the code uses, except where the first-party source agrees (C7 confirms 1 credit per plain
scrape and +4 for JSON). The playbook's "free" for p_search contradicts S11; S11 wins.
