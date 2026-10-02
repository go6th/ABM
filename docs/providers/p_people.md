# p_people: people by title, profiles, posts and comments (INTERNAL)

Vendor name: `config/providers.yaml` `display_name`. Source retrieved **2026-10-02**: `S` = `docs/providers/sources/p_people/api-reference.md` (from `https://docs.bitscale.ai/ingredients/bitscale-api-reference.md`). The published OpenAPI link (`/api-reference/openapi.json`) serves a template "Plant Store" document, not this API, so it was not used.

## What is sourced (transport only)

| Fact | Value | Source |
|---|---|---|
| Plan | API is an Enterprise feature | S header |
| Base URL | `https://api.bitscale.ai/api/v1` | S header table |
| Auth | `X-API-KEY: <workspace key>` | S section 2 |
| Run | `POST /grids/:gridId/run` with `mode` (`sync` default, waits up to 120 s; or `async`), `inputs` (map of column keys), optional `output_columns` (column UUIDs) | S 5.5 |
| Status | `GET /run/status/:requestId`; `status` `running`, `completed`, `failed`; `outputs` keyed by column UUID with `{value, name}` | S 5.6 |
| Side effect | each run **appends a row** to the grid in the provider workspace | S 5.5 |
| Rate limit | 5 requests per second per workspace, all endpoints including polls; `Retry-After` on 429 | S section 3 |
| Errors | 400 (several codes), 401 `INVALID_API_KEY`, 403 `FEATURE_DISABLED`/`GRID_NOT_FOUND`, 404, 429 `RATE_LIMIT_EXCEEDED`, 500 `RUN_FAILED`/`INTERNAL_ERROR` | S section 7 |
| Cost per run | **[VERIFY]** not in the API reference (credits are consumed by the grid's enrichment columns) | — |

## Why the operations are Blocked
The people records come back as **grid columns that the workspace owner defines**, keyed by column UUIDs. There is no public schema for "people by title" or "profile enrichment" output; it depends on how the grid was built. Writing a parser would mean guessing field names, which the build rules forbid. Both operations raise `NotSupportedYet` and the routes return Blocked with a reason.

## What to supply to unblock
Per operation (`people_by_title`, `profile_enrichment`), put in `docs/providers/samples/p_people/`:
1. The grid id.
2. The JSON returned by `GET /api/v1/grids/:gridId/curl` (input column keys, output column UUIDs, names and types).
3. Two or three real run responses (`completed` status), with any personal data you are not allowed to keep replaced by placeholders of the same shape.
4. The plan's credit cost per run, if known.

Also decide (D-03 in the skill register) whether this provider stays the first choice for people by title; the other route option is p_org people search, which needs a title query grammar that is not yet sourced.
