# p_scrape: plain scrape of job pages (INTERNAL)

Vendor name: `config/providers.yaml` `display_name`. Sources retrieved **2026-10-02**; hashes in `sources/INDEX.md`. `S` = `docs/providers/sources/p_scrape/`.

| Fact | Value | Source |
|---|---|---|
| Base URL | `https://api.firecrawl.dev/v2` (we call `https://api.firecrawl.dev` + `/v2/scrape`) | S/v2-openapi.scrape-extract.json `servers` |
| Auth | `Authorization: Bearer <key>` | same file `securitySchemes.bearerAuth` |
| Operation | `POST /scrape` with `url` (required) and `formats` | same file `paths./scrape`, `ScrapeOptions` |
| Formats | array of objects with `type`, e.g. `{"type": "markdown"}` | same file `Formats` |
| Timeout parameter | default 60000 ms, minimum 1000, maximum 300000 | same file `ScrapeOptions.timeout` |
| Response fields relied on | `success` (boolean), `data.markdown`, `data.metadata.sourceURL` (requested), `data.metadata.url` (final URL after redirects), `data.metadata.statusCode` (target page status), `data.metadata.title` (string or list) | same file `ScrapeResponse`; S/scrape-feature.md first JSON example |
| Errors | 400, 402 (`insufficient_credits`), 403 (feature or terms not enabled), 404, 409 (`duplicate_request`/`request_in_flight` for a reused `x-request-id`), 429, 500, 503 (`request_unresolved`, `billing_unavailable`) | same file `paths./scrape.post.responses` |
| Idempotency | `x-request-id` is described only for `alexandria` requests; **[VERIFY]** whether it applies to plain scrapes. Not used. | same file 409/503 descriptions |
| Rate limits | per plan (requests per minute) and concurrent browsers per plan; exceeding either returns 429 | S/rate-limits.md. **[VERIFY]** the account's plan; config holds a conservative placeholder |
| Cost | Scrape 1 credit per page; JSON format +4 per page; Search 2 credits per 10 results plus per-page scrape costs | S/billing.md "Credit costs per endpoint", "Additional credit costs" |
| When charged | a returned document costs credits even when the target returned 403/404; no document returned costs 0 credits | S/billing.md "When credits are charged" |
| Credit to dollar rate | **[VERIFY]** plan-dependent; `usd_per_credit: null` | S/billing.md (plans) |

## Operations implemented
`scrape_job_page` plain markdown only (Done). `json_extraction` and `search_with_scrape` are refused by policy and raise `NotSupportedYet`. Cassette: S/scrape-feature.md first response example, verbatim (it has no `metadata.url`, so the redirect check has nothing to compare and passes).

## Adapter rule
Plain scrapes only, only for `page_kind: job_page`, host on the account allow-list (SSRF checks in `security.py`). A provider-reported final URL off the allow-list is refused after the (billed) call.
