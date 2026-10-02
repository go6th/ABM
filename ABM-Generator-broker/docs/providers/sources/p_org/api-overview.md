> For the complete documentation index, see [llms.txt](https://docs.sumble.com/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.sumble.com/api/api.md).

# Overview

The Sumble API is a RESTful service for programmatic access to Sumble's organization, people, and job-post data. Use it to enrich CRM data, build lead-generation tools, and run market research queries.

## Overview

The API is organized into a few groups of endpoints:

#### [Core data](/api/core-data.md)

Sumble's primary entities. Each is a single composable endpoint: resolve a list you already have, or search by an advanced filter, then use `select` to choose exactly which attributes and metrics come back.

* [Organizations](/api/core-data/organizations.md) — resolve a list of companies or search by technology, job function, industry, and firmographic filters; compose which attributes and per-entity metrics return; retrieve recent organization signals or an organization's confirmed-used tech stack
* [People](/api/core-data/people.md) — resolve a list of people or search by organization and role; compose which attributes return, including contact information (email, phone) and related people (managers, direct reports)
* [Job posts](/api/core-data/jobs.md) — resolve a list of jobs or search by organization and filters; compose which attributes return, including full descriptions, extracted technologies, teams, and functions, and related people (hiring managers, team members)
* [Teams](/api/core-data/teams.md) — resolve a list of teams or search by organization and filters; compose which attributes return, including ICP fit score, related people, and the team's job posts

#### [ID & slug lookups](/api/lookups.md)

Turn human-readable names into the canonical slugs and identifiers the core data endpoints expect in their filters.

* [Technology find](/api/lookups/technologies.md) — search the catalog for technology names and slugs, resolve known names/slugs/aliases to canonical technologies, and resolve technology categories to their constituent technologies
* [Project find](/api/lookups/projects.md) — resolve a list of project names or slugs to canonical identifiers
* [Job function & level lookup](/api/lookups/job-title-lookup.md) — resolve a list of job titles to canonical job function and level identifiers

#### [Intelligence endpoints](/api/intelligence.md)

AI-generated intelligence on the organizations you care about.

* [Signals](/api/intelligence/signals.md) — search recent account signals by organization, person, technology, job function, account list or signal ID; search Priority Signals by organization, person, or job post IDs, or source signal ID.
* [Intelligence briefs](/api/intelligence/intelligence-briefs.md) — generate an AI account brief for an organization

#### [Lists](/api/lists.md)

Create, manage, and access your saved lists.

* [Organization lists](/api/lists/organization-lists.md) — create, manage, and access saved organization lists
* [Contact lists](/api/lists/contact-lists.md) — create, manage, and access saved people lists

#### [Support](/api/support.md)

Report data quality issues and submit general support requests programmatically.

* [Support](/api/support.md) — report a data quality issue (`POST /support/data-quality`) or submit a general support request (`POST /support`); both are free and reply to your account email

#### [MCP](/api/mcp.md)

* [MCP](/api/mcp.md) — connect Sumble to Claude, Cursor, ChatGPT, and other MCP clients

## Getting started

### 1. Generate your API key

Go to [sumble.com/account/api-keys](https://sumble.com/account/api-keys) and create a new key. Store it securely — it won't be shown again.

### 2. Base URL

All endpoints are served from:

```
https://api.sumble.com/v9/
```

{% hint style="info" %}
This is the only base URL for the Sumble API. If you have seen a `sumble.com/paid-api/...` path in an older example or a support thread, replace it with `https://api.sumble.com/` — that path is not a supported public endpoint.
{% endhint %}

### 3. Authenticate

Every request requires your API key as a Bearer token in the `Authorization` header:

```
Authorization: Bearer YOUR_API_KEY
```

### 4. Make your first request

This request resolves an organization by domain and composes exactly the fields it returns — baseline attributes plus a per-technology metric:

```bash
curl https://api.sumble.com/v9/organizations \
  --request POST \
  --header "Authorization: Bearer $API_KEY" \
  --header 'Content-Type: application/json' \
  --data '{
    "organizations": [
      { "url": "stripe.com" }
    ],
    "select": {
      "attributes": ["name", "industry", "employee_count"],
      "entities": [
        { "type": "technology", "term": "python", "metrics": ["job_post_count"] }
      ]
    }
  }'
```

### 5. Compose your response

Most endpoints follow the same pattern: provide either a **list** of entities to resolve or a **filter** to search, then use the `select` block to request exactly the `attributes` — and, where supported, per-entity `entities` metrics — you want back. You only pay for what you request (see [Credits](#credits)). Each endpoint page documents its full set of inputs and selectable fields.

### Next steps

* Browse the [endpoint reference](#overview) for inputs, response fields, and examples
* Prefer to work through an AI client? Set up [MCP](/api/mcp.md)
* Review [credit costs](#credits) before running large jobs

## File downloads

Most endpoints that return rows accept a `file_name` parameter. Set it and the response carries file metadata and an authenticated Sumble download URL in place of the inline rows, at the same credit cost. Opening the download URL requires the file owner's Sumble web session.

End the name in `.csv`, `.xlsx` or `.json`. All three hold the same results. They differ in how those results are shaped and typed:

* `.csv` is plain UTF-8 text, so every cell is text. A value starting with `=`, `+`, `-` or `@` gets a leading apostrophe, which stops a spreadsheet running it as a formula.
* `.xlsx` is an Excel workbook with a single sheet, named for what it holds. Numbers stay numeric and booleans stay boolean, so sorting and arithmetic work without retyping a column, and text that looks like a formula keeps its exact characters. Excel caps a cell at 32,767 characters and longer values are truncated.
* `.json` is an array of objects, indented and UTF-8, holding the same results the response would have returned inline. Nothing is quoted, truncated or flattened to suit a spreadsheet, so this is the format to take when the results are going into a program.

**Row 1 of an `.xlsx` is a title bar** naming the Sumble surface the file came from, who asked for it and when. **The column names are on row 2 and the data starts on row 3**, with the header row frozen and filterable. Reading one with a library that assumes the first row is the header needs that offset, for example `pandas.read_excel(path, header=1)`. A `.csv` has no title row: its header is the first line.

**An `.xlsx` folds each Sumble link into the name it belongs to.** Where a `.csv` has a `sumble_url` column beside `name`, the workbook makes the name itself a link and drops the URL column. The same goes for a prefixed pair, such as `organization_name` with `organization_sumble_url`. Excel shows the link, but a library that reads cell values does not: `pandas.read_excel` gives you the name without the URL. Take the `.csv` or the `.json`, or the inline rows, when you need the URL as data. The `columns` list in the response metadata always describes what that file actually holds.

**A `.csv` and an `.xlsx` are flat; a `.json` is not.** A sheet has one row per result and no nested columns, so the two spreadsheet formats open an object out into prefixed columns — a job's `organization` becomes `organization_id`, `organization_name`, `organization_domain` — and write a repeated value, such as a person's experience or a job's technologies, as compact JSON text in a single cell. A `.json` keeps the structure the inline response has, so `organization` is an object and `technologies` is an array. If you are parsing those cells back out, take the `.json` instead.

Two consequences for the response metadata. `columns` on a `.json` lists the top-level keys of each object, not the sheet's column names, and `row_count` counts the top-level results — for the tech stack, that is the business functions, where the sheet has one row per evidence technology.

## Credits

API calls consume credits from your account. Costs vary by endpoint:

| Endpoint                                                                                   | Cost                                                                                                                                                                                                                                                           |
| ------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Organizations](/api/core-data/organizations.md)                                           | Per organization: 1 base credit + 1 per paid attribute + the metrics requested per entity. Unmatched inputs are free.                                                                                                                                          |
| [Organization signals](/api/core-data/organizations/organization-signals.md)               | 1 credit per signal returned. Organizations with no returned signals are free.                                                                                                                                                                                 |
| [Organization tech stack](/api/core-data/organizations/organization-tech-stack.md)         | 1 credit per technology returned. Organizations with no confirmed-used technologies are free.                                                                                                                                                                  |
| [Signals search](/api/intelligence/signals.md)                                             | 1 credit per signal returned. Searches with no returned signals are free.                                                                                                                                                                                      |
| [Priority Signals search](/api/intelligence/signals.md#search-priority-signals)            | 1 credit per priority signal returned. Searches with no returned priority signals are free.                                                                                                                                                                    |
| [Intelligence briefs](/api/intelligence/intelligence-briefs.md)                            | 50 credits per completed brief                                                                                                                                                                                                                                 |
| [People](/api/core-data/people.md)                                                         | Per person: 1 base credit + 1 per paid attribute + 1 per related person returned. Unmatched inputs are free. Contact reveals cost extra (10 credits per email, 80 per phone, first reveal only) — see the [People page](/api/core-data/people.md#credit-cost). |
| [Job posts](/api/core-data/jobs.md)                                                        | Per job: 1 base credit + 1 per paid attribute + 1 per related person returned. Unmatched inputs are free.                                                                                                                                                      |
| [Teams](/api/core-data/teams.md)                                                           | Per team: 1 base credit + 1 per paid attribute + 1 per related person + 1 per job post returned. Unmatched inputs are free.                                                                                                                                    |
| [Job function & level lookup](/api/lookups/job-title-lookup.md)                            | 1 credit per 100 matched titles                                                                                                                                                                                                                                |
| [Organization lists](/api/lists/organization-lists.md)                                     | 1 credit per list or organization returned                                                                                                                                                                                                                     |
| [Create / add to list](/api/lists/organization-lists.md)                                   | No credit cost                                                                                                                                                                                                                                                 |
| [Contact lists](/api/lists/contact-lists.md)                                               | 1 credit per list or person returned                                                                                                                                                                                                                           |
| [Create / add to contact list](/api/lists/contact-lists.md)                                | No credit cost                                                                                                                                                                                                                                                 |
| Technologies find                                                                          | 1 credit if results found                                                                                                                                                                                                                                      |
| [Technologies lookup](/api/lookups/technologies.md#look-up-technologies)                   | 1 credit per 100 matched technologies                                                                                                                                                                                                                          |
| [Technology categories lookup](/api/lookups/technologies.md#look-up-technology-categories) | 1 credit per 100 matched categories                                                                                                                                                                                                                            |
| [Projects lookup](/api/lookups/projects.md#look-up-projects)                               | 1 credit per 100 matched projects                                                                                                                                                                                                                              |

A `technology_category` entity with `granularity: "exploded"` counts each technology in the category individually — a category with 3 technologies multiplies that entity's metric cost by 3.

See [Credits](/web-app/exports-credits/credits-and-exports.md) for details on monthly allocations and purchasing additional credits.

## Rate limits

API usage is subject to rate limiting to ensure service stability. Each user can do 10 requests per second (aggregated through all the endpoints), allowing occasional bursts.

Once the rate limit is reached, the API responds with response code 429.

## Timeouts

A request can run for up to 55 seconds. After that the API stops it and responds with status code 504 and this body:

```json
{
  "detail": "The request took longer than 55 seconds and was stopped. No credits were charged. Retry it, or send fewer items per request.",
  "type": "request_timeout"
}
```

A request that times out is not charged. Set your client timeout to 65 seconds or more. A client that disconnects earlier is still charged if the request completes.

If a request times out, retry it. If it keeps timing out, send fewer items per request, for example fewer organizations or fewer entities per call.

## Error responses

| Status Code | Meaning                                   |
| ----------- | ----------------------------------------- |
| 200         | Success                                   |
| 202         | Accepted, in progress — retry soon        |
| 400         | Bad request — check your request body     |
| 401         | Unauthorized — invalid or missing API key |
| 402         | Insufficient credits                      |
| 429         | Rate limited — wait and retry             |
| 500         | Server error — contact support            |
| 504         | Timed out, no credits charged. Retry      |
