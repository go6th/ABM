# p_org: organizations, tech stack, people by technology (INTERNAL)

Vendor name: `config/providers.yaml` `display_name`. Sources retrieved **2026-10-02**; hashes in `sources/INDEX.md`. `S` = `docs/providers/sources/p_org/`; `O` = `S/v9-openapi.extract.json` (from `https://api.sumble.com/openapi.json?version=v9`).

| Fact | Value | Source |
|---|---|---|
| Base URL | `https://api.sumble.com` with `/v9/...` paths | S/api-overview.md "Base URL"; O `servers` |
| Auth | `Authorization: Bearer <key>` | S/api-overview.md "Authenticate"; O `securitySchemes` |
| Organization resolve | `POST /v9/organizations` with `organizations: [{url}]` and `select.attributes`; one result row per entry in input order, each echoing `input`; unmatched rows have empty `attributes` | O `EnrichOrganizationsRequest`, 200 example |
| Free org attributes | `id`, `name`, `slug`, `url` and the provider page URL field | S/organizations.md "Credit cost" |
| Tech stack | `GET /v9/organizations/{organization_id}/techs`; `business_functions[].categories[].technologies[]` (`name`, `slug`, `job_post_used_count`) | O path and 200 example; S/organization-tech-stack.md |
| People | `POST /v9/people` filter mode: `filter.organization_ids`, `filter.query.query`, `select.attributes`, `limit` (default 10, max 200), `offset` | S/people.md "Input", "Advanced query"; O `EnrichPeopleRequest` |
| Technology query | `technology EQ '<slug>'` | O `PersonAttributes.technologies` description |
| People is asynchronous (v8+) | start returns `request_id` and `status: pending`; poll with `{"request_id"}` alone; statuses `pending`, `running`, `succeeded`, `failed` | S/people.md "Asynchronous requests" |
| People charging | once, on the first `succeeded` poll; polling free; `failed` costs nothing | S/people.md "When you're charged" |
| Paging rule | continue until `offset + limit >= total`; short pages can occur | S/people.md "Search" |
| Hosted files | `file_name` returns a hosted download instead of rows | S/api-overview.md "File downloads". **Never sent** (adapter rule); a response with `file` is refused |
| Credits | organizations: 1 per org + 1 per paid attribute + metrics; tech stack: 1 per technology returned; people: 1 per person + 1 per paid attribute (`name` free) + 1 per related person | S/api-overview.md "Credits"; S/people.md "Credit cost" |
| Credits reported | `credits_used`, `credits_remaining` on responses | O response schemas |
| Rate limit | 10 requests per second per user, bursts allowed; 429 when exceeded | S/api-overview.md "Rate limits" |
| Timeout | up to 55 s server side, then 504 with no charge; client timeout 65 s or more; a client that disconnects earlier is still charged | S/api-overview.md "Timeouts" |
| Errors | 202 accepted (retry soon), 400, 401, 402 insufficient credits, 429, 500, 504 (no charge) | S/api-overview.md "Error responses" |
| Retry-After | **[VERIFY]** not documented | — |
| Dollar per credit | **[VERIFY]** plan-dependent; `usd_per_credit: null` | S/api-overview.md links to plans |

## Operations implemented
`org_resolve`, `org_tech_stack`, `people_by_technology` (Done). `org_teams` raises `NotSupportedYet`: the spec page for teams was not written in this work package.

Cassettes: the OpenAPI 200 examples (organizations, techs, people) verbatim, plus `people_start` (quoted from people.md) and `people_poll_succeeded` (the people example plus `request_id` and `status: succeeded`, the two async fields people.md documents). The documented start example omits the `id` field the OpenAPI marks required, so the connector does not rely on `id`.

## Resume without a duplicate
When a people job was started and its `request_id` recorded (`submitted` event), a resumed worker polls that job instead of starting another, so no second charge occurs.
