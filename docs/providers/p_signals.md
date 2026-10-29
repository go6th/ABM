# p_signals: company news, job openings, technology detections (INTERNAL)

Vendor name: `config/providers.yaml` `display_name`. Source: the vendor's OpenAPI 3.1 document, retrieved **2026-10-02** from `https://docs.predictleads.com/schemas/open_api_schema.json` (hash in `sources/INDEX.md`); extract `S` = `sources/p_signals/v3-openapi.company-feeds-extract.json`.

| Fact | Value | Source |
|---|---|---|
| Base URL | `https://predictleads.com/api/v3` | S `servers` |
| Auth | headers `X-API-KEY` and `X-API-TOKEN` (also basic or OAuth client credentials) | S `security`, `securitySchemes` |
| News | `GET /companies/{company_id_or_domain}/news_events`; filters `found_at_from`, `found_at_until`, `categories`, `location` | S `paths` |
| Jobs | `GET /companies/{company_id_or_domain}/job_openings`; filters incl. `first_seen_at_from`, `active_only`, `not_closed` | S `paths` |
| Tech | `GET /companies/{company_id_or_domain}/technology_detections`; filters incl. `first_seen_at_from` | S `paths` |
| Pagination | `page` (default 1, min 1), `limit` (default 100, max 1000); `meta.count` is returned only when `page` is sent | S parameters; info.description "Pagination" |
| Response envelope | JSON:API: `data[]` (`id`, `type`, `attributes`, `relationships`), `included[]`, `meta` (`schema_version`, `record_state`, `count`) | S `NewsEventsDataset`, `JobOpeningsDataset`, `TechnologyDetectionsDataset` |
| News fields relied on | `summary`, `category`, `found_at`, `confidence`, `article_sentence`, `planning`, `amount_normalized`, `effective_date`; `relationships.most_relevant_source` to an included `news_article` (`url`, `title`, `published_at`) | S `NewsEvent` |
| Job fields relied on | `title`, `normalized_title`, `description`, `url`, `first_seen_at`, `last_seen_at`, `posted_at`, `status` (null or "closed"), `seniority`, `location`, `categories` | S `JobOpening` |
| Tech fields relied on | `first_seen_at`, `last_seen_at`, `behind_firewall`, `score`, `source_count`; relationships `technology` (included `name`), `seen_on_subpages` (`subpage_url`), `seen_on_job_openings` (`url`) | S `TechnologyDetection` |
| Errors | 400, 401 (credentials or subscription), **402 monthly request limit exceeded**, **403 request rate over limit**, 404 record not found, 422, 429 with `Retry-After` (seconds), 500, 503 | S responses on each path |
| Rate limit (numeric) | **[VERIFY]** not published; config holds a conservative placeholder | S (absent) |
| Cost model | requests per month limited by plan; each page is one request | S info.description "Request Limits" |
| Dollar cost | **[VERIFY]** plan-dependent; `usd_per_credit: null` | — |

## Operations implemented
`news_events`, `job_openings`, `tech_detections` (Done). Cassettes are the three dataset `example` objects from the OpenAPI, verbatim.

## Pull once
Arguments are the domain and an optional delta date (`since` maps to `found_at_from` or `first_seen_at_from`); page size is fixed by config (`page_limit`, the documented maximum 1000). The same feed in the same run always hashes to the same per-page ledger keys, so a second request replays stored pages. A delta pull is a new key.

## Job openings filters
`FeedArgs.with_description_only` (default on) and `FeedArgs.active_only` (default off) are sent for `job_openings` only, as the documented `with_description_only` and `active_only` parameters. The job pipeline's incremental re-research pulls the delta with `first_seen_at_from` = the previous research minus an overlap and refreshes carried statuses with one `active_only` pull (`src/jobs/incremental.py`).

## Note on 403
For this provider 403 means rate limited, not forbidden. The connector maps it to `RateLimited` and sets the shared cooldown.
