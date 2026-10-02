# p_search: open web search and known-page reads (INTERNAL)

Vendor name: see `config/providers.yaml` `display_name` (internal only).
All sources retrieved **2026-10-02** with `curl`; files and SHA-256 in `sources/INDEX.md`.
`S` = `docs/providers/sources/p_search/`.

| Fact | Value | Source |
|---|---|---|
| Base URL | `https://api.parallel.ai` | S/search.openapi.md `servers` |
| Auth | header `x-api-key: <key>` | S/search.openapi.md `securitySchemes.ApiKeyAuth` |
| Search op | `POST /v1/search` | S/search.openapi.md `paths./v1/search` |
| Search request fields used | `search_queries` (required, array of strings, "3-6 words each"), `objective` (optional), `mode` (`turbo`, `fast`, `basic`, `advanced`; default `advanced`), `advanced_settings.max_results` (default 10), `advanced_settings.source_policy.include_domains`, `after_date` (YYYY-MM-DD) | S/search.openapi.md `V1SearchRequest`, `AdvancedSearchSettings`, `SourcePolicy` |
| Search response fields relied on | `search_id` (required), `results[]` (required) with `url` (required), `excerpts` (required), `title` (optional, nullable), `publish_date` (optional, nullable) | S/search.openapi.md `V1SearchResponse`, `V1WebSearchResult` |
| Read op | `POST /v1/extract` | S/extract.openapi.md `paths./v1/extract` |
| Read request fields used | `urls` (required, up to 20; we send 1), `objective` (optional), `advanced_settings.full_content` (boolean, default false) | S/extract.openapi.md `V1ExtractRequest`, `AdvancedExtractSettings` |
| Read response fields relied on | `extract_id`, `results`, `errors` (all required); result `url`, `excerpts` (required), `title`, `publish_date`, `full_content` (optional); error `url`, `error_type`, `http_status_code` (required) | S/extract.openapi.md `V1ExtractResponse`, `V1ExtractResult`, `ExtractError` |
| Pagination | none (single response per call) | both OpenAPI documents (no cursor fields) |
| Errors | 401 auth (no retry), 402 insufficient credit (no retry), 403 forbidden (no retry), 404, 408 retry, 422 validation (no retry), 429 retry, 500/502/503 retry | S/errors.md "Error Reference" |
| Retry-After on 429 | **[VERIFY]** not documented; connector uses the configured default cooldown | S/errors.md (absent) |
| Rate limits | Search 600 per min; Extract 600 per min | S/rate-limits.md table |
| Cost: search | `turbo`/`fast` $0.001 per request; `basic`/`advanced` $0.005; +$0.001 per additional result beyond 10 | S/pricing.md "Search API" table and formulas |
| Cost: read | $0.001 per URL ($1 per 1,000 URLs) | S/pricing.md "Extract API" |
| Billing of failed Search/Extract calls | **[VERIFY]** not stated (only Task and Responses say failures are not billed); config `bill_on_failure: false` | S/pricing.md |
| Idempotency key | none documented | both OpenAPI documents |

## Operations implemented
`web_search` (Done), `read_page` (Done). Cassettes: `contract/cassettes/p_search/` are the OpenAPI 200 examples, verbatim.

## Discrepancy with the PRD playbook
The playbook says this provider is free ("one session per run"). The current pricing page lists per-request prices. The config uses the pricing page and keeps the playbook text as `prd_hint`.
