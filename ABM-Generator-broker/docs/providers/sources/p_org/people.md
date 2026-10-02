> For the complete documentation index, see [llms.txt](https://docs.sumble.com/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.sumble.com/api/core-data/people.md).

# People

## Find, match, and enrich people

The single endpoint for working with people. Provide either a **list of people** to resolve (list mode) or a **filter** to find people using structured criteria or plain-language search, and compose exactly which attributes you get back. This replaces the previous `find`, `find-related-people`, and `enrich` endpoints.

### Input

Provide exactly one of:

* **`people`** — a list of up to 1,000 people to resolve, each identified by a Sumble `person_id`, a `linkedin_url`, or a work `email` (used in that order of precedence). Limited to 25 entries when requesting contact attributes, related people, or email identifiers. Covers the previous `enrich` and `find-related-people` flows.
  * Entries identified by `email` may also carry an optional **`name`**. It isn't an identifier — an entry with only a name is rejected — but it noticeably improves email resolution, especially for addresses like `firstname@company.com` where the name can't be read off the address, and for people whose email is at a former employer. Include it whenever you have it.
* **`filter`** selects people using an advanced query or search. Use `organization_ids`, up to 1,000, and/or a saved `organization_list_id` to scope either approach to those organizations. Covers the previous `find` flow.

#### Advanced query

`filter.query` matches people using a structured query expression over job functions, technologies, job levels, locations, and more. Provide an organization scope.

In filter mode, `limit` (default 10, max 200) and `offset` page through results, and `order_by_column` sorts by `start_date`, `job_level`, or `person_score`. Sorting by `person_score` requires exactly one organization in scope and an ICP configured for your account.

#### Search

Search is in limited beta. Contact <support@sumble.com> for access.

Use `filter.search` to describe responsibilities or experience in plain language. Results are ranked by relevance.

For search, `requested_total` sets the maximum number of people across all pages, from 1 to 1,000. It defaults to `limit`, so a request with `limit: 10` returns at most ten people in total. To request 100 people in pages of 20, set `requested_total: 100` and `limit: 20` from the first request. Keep the search, scope, and `requested_total` unchanged when paging, and increase `offset` by `limit`.

Results include the best available matches, which may only partly match your description. `total` is the size of this ranked list, capped by `requested_total`, rather than a count of everyone who might match. A short or empty page can mean some profiles are unavailable. Continue until `offset + limit >= total`.

```json
{
  "filter": {
    "search": "Own Salesforce administration and revenue operations",
    "organization_ids": [10441, 27730]
  },
  "select": { "attributes": ["name", "job_title"] },
  "limit": 25,
  "offset": 0
}
```

Omit the organization filters to search across Sumble's people records. With an organization scope, add `"include_past_employers": true` to `filter` to include former employees. This option requires `search`.

`search` cannot currently be combined with `query`, `order_by_column`, or the `person_score` attribute.

### Composable response

The `select` block dictates exactly which attributes return — nothing is mandatory:

* **`attributes`** — a list of person attributes, or `"all"`. Available: `name`, `linkedin_url`, `job_title`, `job_function`, `job_level`, `location`, `country`, `current_employer`, `technologies`, `person_score`, the profile attributes `experience` and `summary`, and the contact attributes `email` and `phone`. `person_id` and `sumble_url` are always included for free.
  * The contact attributes `email` and `phone` must be requested explicitly — they are never part of `"all"` — and are only available in list mode, for up to 25 people per request.
  * The profile attributes must also be requested explicitly. They work in both modes; a list-mode request selecting them is capped at 200 people, matching the search-mode page size. `experience` is the person's LinkedIn-style role history, newest first: `title`, `start_date` and `end_date` (`YYYY-MM`, null end for a current role), `duration`, `location`, the person's own `description` of the role, and an `employer` shaped like `current_employer` (`organization_id`, `slug`, and `sumble_url` when Sumble knows the organization, plus the employer's `name` and `linkedin_url` from the profile). `summary` is the profile's "About" section as the person wrote it.
  * `person_score` is your ICP match score (0–100) with its contribution breakdown. It requires filter mode with exactly one organization in scope and an ICP configured; `"all"` includes it opportunistically when those requirements are met.
* **`related_people`** — inferred managers and/or direct reports for each matched person. Choose the `direction` (`managers`, `direct_reports`) and which attributes to return for them. Available in list mode only, for up to 25 people per request.

### File output

On v9, add `file_name` to the start request to receive the completed result as a downloadable file instead of inline `people`. The value must be a filename ending in `.csv`, `.xlsx` for an Excel workbook, or `.json` — for example `people.csv`, `people.xlsx` or `people.json`.

A `.json` file contains one object per person in the same structure as the inline `people` results, so the current employer and person score stay objects and technologies, experience, managers, and direct reports stay arrays.

A `.csv` or `.xlsx` file contains one row per person. In list mode, unmatched inputs remain as rows with their `input_*` columns populated. Person, current-employer, and person-score scalar values have their own columns. Repeated or nested values — technologies, experience, matched score features, managers, and direct reports — are compact JSON in one cell.

Pass `file_name` only when starting the request. Poll with `request_id` alone. A successful poll returns `people: []` and a `file` object with the filename, columns, row count, size, and authenticated download URL.

If the file cannot be written, the request still succeeds and returns the rows inline with no `file`. A download problem never costs you a result you have already paid for, so handle both shapes.

### Credit cost

Charged per matched person (list mode) or per returned person (search mode). Unmatched inputs are free.

Per person the cost is:

* **1 base credit**, plus:
* **1 credit per paid attribute** requested (`name` is free), plus
* **1 credit per related person** returned.

Contact and email-identifier charges apply on top:

* **Email reveal**: 10 credits the first time your account reveals an email for a given person; repeat requests while the reveal is active are free, and nothing is charged when no email is found.
* **Phone reveal**: 80 credits the first time your account reveals a phone for a given person, with the same repeat and not-found rules.
* **Email identifiers**: resolving an input `email` to a person costs 20 credits per unique email that resolves; emails that don't resolve are free.

For example, returning 10 people with 2 paid attributes each costs `10 × (1 + 2) = 30` credits. The API checks affordability up front and returns HTTP `402` before doing any work you can't pay for.

## Asynchronous requests

`POST /people` is **asynchronous** in both modes: it starts a background job and returns immediately with a `request_id`. You then poll the same endpoint with that `request_id` to retrieve the status and, once ready, the result.

{% stepper %}
{% step %}

### Start the request

`POST /people` with a `people` or `filter` body exactly as described above. The response has `status: "pending"` and a `request_id`. No credits are charged yet, but the request is fully validated and your balance is pre-checked up front, so you still receive the usual `400`/`402` immediately — for example if `person_score` is misconfigured or you can't afford the worst-case cost.
{% endstep %}

{% step %}

### Poll for the result

`POST /people` with `{ "request_id": "..." }`. While the job runs you get `status: "pending"` or `"running"`. When it finishes you get a terminal status and, on success, the full result (`people`, `total`, and the other fields).
{% endstep %}
{% endstepper %}

Asynchronous list requests also let Sumble fall back to a deeper reverse-enrichment lookup for input `email`s it can't resolve immediately — those results are filled in before the job completes, rather than coming back unmatched.

### Poll request body

| Field        | Type   | Description                                                                                                                                 |
| ------------ | ------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `request_id` | string | The `request_id` returned when the job was started. Provide this **alone** to poll — don't send `people`, `filter`, or `file_name` with it. |

### Job status values

| `status`    | Meaning                                               |
| ----------- | ----------------------------------------------------- |
| `pending`   | Queued, not started yet.                              |
| `running`   | The request is being processed.                       |
| `succeeded` | Done; the result fields are included in the response. |
| `failed`    | The request errored after retries.                    |

### When you're charged

Credits are charged **once, on the first poll that returns `succeeded`**. `failed` jobs cost nothing. Polling is always free: the `credits_used` field reflects what this particular call charged, so the successful poll reports the total and every subsequent poll of the same job reports `0`.

### Async response fields

Alongside the usual result fields, the response includes:

| Field        | Type   | Description                                                                                                |
| ------------ | ------ | ---------------------------------------------------------------------------------------------------------- |
| `request_id` | string | Id of the async job. Poll `POST /people` with this id to retrieve the result.                              |
| `status`     | string | Job status — one of `pending`, `running`, `succeeded`, or `failed`.                                        |
| `file`       | object | v9 only. Metadata and download URL for the file requested with `file_name`; present when the job succeeds. |

### Example: start then poll

{% tabs %}
{% tab title="Start" %}

```bash
curl -X POST https://api.sumble.com/v9/people \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{ "people": [ { "email": "lou@sumble.com" } ], "select": { "attributes": ["name", "job_title"] } }'
# -> { "request_id": "019d...", "status": "pending", "credits_used": 0, "credits_remaining": 6571 }
```

{% endtab %}

{% tab title="Poll" %}

```bash
curl -X POST https://api.sumble.com/v9/people \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{ "request_id": "019d..." }'
# -> { "request_id": "019d...", "status": "succeeded", "credits_used": 4, "people": [ ... ], "total": 1 }
```

{% endtab %}
{% endtabs %}

## Unified people endpoint

> Find, match, and enrich people. Provide a \`people\` list (list mode) or a \`filter\` (search mode) and compose the response with \`select\`.\
> \
> On \*\*v7 and earlier\*\* this endpoint is synchronous: one call returns the people.\
> \
> On \*\*v8 and later\*\* it is \*\*asynchronous\*\* in both modes. Send a \`people\` or \`filter\` body to start a background job — the response returns a \`request\_id\` and \`status: "pending"\`, and no credits are charged yet (the request is validated and your balance pre-checked, so you still get a \`400\`/\`402\` up front). Then send \`{ "request\_id": "..." }\` to the same endpoint to poll: you get \`status\` \`pending\`/\`running\` until it finishes, then \`succeeded\` (with the full \`people\`/\`total\` result) or \`failed\`. Credits are charged once, on the first \`succeeded\` poll; repeat polls report \`credits\_used: 0\`. The async response adds \`request\_id\` and \`status\` fields.

```json
{"openapi":"3.1.0","info":{"title":"Sumble API","version":"v9"},"servers":[{"url":"https://api.sumble.com"}],"security":[{"api_token":[]}],"components":{"securitySchemes":{"api_token":{"type":"http","scheme":"bearer"}},"schemas":{"EnrichPeopleRequest":{"properties":{"people":{"anyOf":[{"items":{"$ref":"#/components/schemas/PersonInput"},"type":"array","maxItems":1000,"minItems":1},{"type":"null"}],"title":"People","description":"List of people to resolve and enrich. One result row per entry, in input order. Mutually exclusive with `filter`."},"filter":{"anyOf":[{"$ref":"#/components/schemas/PeopleFilter"},{"type":"null"}],"description":"Query selecting the people to return. Mutually exclusive with `people`."},"select":{"$ref":"#/components/schemas/PeopleSelect","description":"Dictates exactly which attributes are returned."},"limit":{"type":"integer","maximum":200,"minimum":1,"title":"Limit","description":"Maximum number of people to return (filter mode only; ignored when `people` is provided).","default":10},"offset":{"type":"integer","maximum":10000,"minimum":0,"title":"Offset","description":"Number of results to skip (filter mode only; ignored when `people` is provided).","default":0},"order_by_column":{"anyOf":[{"type":"string","enum":["person_score","start_date","job_level"]},{"type":"null"}],"title":"Order By Column","description":"Column to order by (filter mode only): `start_date` (current role start), `job_level` (seniority rank), or `person_score`. `person_score` sorts people by how well they match your ideal customer profile (ICP) and includes — and charges for — the `person_score` attribute on every row; it requires the filter to resolve to exactly one organization and an ICP configured for your account, and only supports descending order. Default: Sumble's standard people ordering."},"order_by_direction":{"anyOf":[{"type":"string","enum":["ASC","DESC"]},{"type":"null"}],"title":"Order By Direction","description":"Sort direction; DESC when omitted."},"request_id":{"anyOf":[{"type":"string"},{"type":"null"}],"title":"Request Id","description":"(v8 only) Id of a previously started async request (returned by the kickoff call). Provide this alone to poll for the status and result."}},"additionalProperties":false,"type":"object","title":"EnrichPeopleRequest"},"PersonInput":{"properties":{"person_id":{"anyOf":[{"type":"integer","minimum":1},{"type":"null"}],"title":"Person Id","description":"Person id on Sumble. When set, it identifies the person directly and `linkedin_url`/`email` are ignored."},"linkedin_url":{"anyOf":[{"type":"string"},{"type":"null"}],"title":"Linkedin Url","description":"LinkedIn profile URL of the person, e.g. 'https://www.linkedin.com/in/<slug>'. Entries whose URL is not a profile URL come back unmatched. Takes precedence over `email`."},"email":{"anyOf":[{"type":"string"},{"type":"null"}],"title":"Email","description":"Work email of the person, resolved via reverse enrichment. Costs 20 extra credits when it resolves to a returned person; emails that don't resolve come back unmatched, free. Only used when neither `person_id` nor `linkedin_url` is provided."},"name":{"anyOf":[{"type":"string"},{"type":"null"}],"title":"Name","description":"Full name of the person, used only to disambiguate the reverse-enrichment lookup when resolving by `email`. Not an identifier on its own — an entry with just a name is rejected — and ignored for `person_id`/`linkedin_url` entries. Supplying it materially improves resolution for addresses whose local part isn't a parseable name (e.g. `firstname@company.com`), and lets the matcher recognise people whose email is at a former employer."}},"additionalProperties":false,"type":"object","title":"PersonInput"},"PeopleFilter":{"properties":{"organization_ids":{"anyOf":[{"items":{"type":"integer"},"type":"array","maxItems":1000,"minItems":1},{"type":"null"}],"title":"Organization Ids","description":"Sumble organization ids to search within. People queries are organization-scoped: provide this and/or `organization_list_id`."},"organization_list_id":{"anyOf":[{"type":"integer","minimum":1},{"type":"null"}],"title":"Organization List Id","description":"Id of one of your saved organization lists (see /organization-lists) whose organizations to search within. Combined with `organization_ids` when both are provided."},"query":{"anyOf":[{"$ref":"#/components/schemas/Query"},{"type":"null"}],"description":"Advanced query narrowing the people returned (job functions, technologies, job levels, locations, ...)."}},"additionalProperties":false,"type":"object","title":"PeopleFilter"},"Query":{"properties":{"query":{"type":"string","title":"Query"}},"type":"object","required":["query"],"title":"Query"},"PeopleSelect":{"properties":{"attributes":{"anyOf":[{"items":{"type":"string","enum":["name","email","phone","linkedin_url","job_title","job_function","job_level","location","country","current_employer","technologies","person_score","confidence"]},"type":"array"},{"type":"string","const":"all"}],"title":"Attributes","description":"Person-level attributes to include, or 'all' for every attribute-priced attribute. Off by default. 'all' excludes contact attributes (email, phone), which must be requested explicitly, and includes `person_score` only when it is available (filter mode with exactly one organization in scope and an ICP configured for your account) — otherwise it is skipped, not an error. When `person_score` is included and no `order_by_column` is set, results are ordered by it. Note that 'all' grows — and its cost grows — as new attributes are added to the endpoint.","default":[]},"related_people":{"anyOf":[{"$ref":"#/components/schemas/RelatedPeopleSelection"},{"type":"null"}],"description":"Also return people related to each matched person, up or down the org hierarchy. Relationships are inferred from org structure and seniority (technology, job function, team, location and job-level similarity), not actual reporting lines. Only available in match mode (`people`), for at most 25 people per request. Each related person returned costs 1 credit."}},"additionalProperties":false,"type":"object","title":"PeopleSelect"},"RelatedPeopleSelection":{"properties":{"direction":{"items":{"type":"string","enum":["managers","direct_reports"]},"type":"array","minItems":1,"title":"Direction","description":"Which hierarchy direction(s) to return."},"attributes":{"anyOf":[{"items":{"type":"string","enum":["name","email","phone","linkedin_url","job_title","job_function","job_level","location","country","current_employer","technologies","person_score","confidence"]},"type":"array"},{"type":"string","const":"all"}],"title":"Attributes","description":"Person-level attributes to include for each related person, or 'all' for every attribute available here. Contact attributes (email, phone) and `person_score` are not available here; `confidence` (the inferred relationship's score breakdown) is only available here.","default":[]}},"additionalProperties":false,"type":"object","required":["direction"],"title":"RelatedPeopleSelection"},"EnrichPeopleResponse":{"properties":{"id":{"type":"string","format":"uuid","title":"Id"},"credits_used":{"type":"integer","title":"Credits Used"},"credits_remaining":{"type":"integer","title":"Credits Remaining"},"request_id":{"anyOf":[{"type":"string"},{"type":"null"}],"title":"Request Id","description":"(v8 only) Id of the async request. Poll POST /people with this id to retrieve the status and result."},"status":{"anyOf":[{"type":"string"},{"type":"null"}],"title":"Status","description":"(v8 only) Request status: pending, running, succeeded, or failed. Null on the synchronous (<= v7) endpoint."},"people":{"anyOf":[{"items":{"$ref":"#/components/schemas/PersonRow"},"type":"array"},{"type":"null"}],"title":"People"},"matched_count":{"anyOf":[{"type":"integer"},{"type":"null"}],"title":"Matched Count","description":"List mode only: how many input entries resolved to a Sumble person."},"total":{"anyOf":[{"type":"integer"},{"type":"null"}],"title":"Total"},"source_data_url":{"anyOf":[{"type":"string","maxLength":2083,"minLength":1,"format":"uri"},{"type":"null"}],"title":"Source Data Url"}},"type":"object","required":["id","credits_used","credits_remaining"],"title":"EnrichPeopleResponse"},"PersonRow":{"properties":{"input":{"anyOf":[{"$ref":"#/components/schemas/PersonInput"},{"type":"null"}],"description":"The input entry this row corresponds to (list mode only)."},"person_id":{"anyOf":[{"type":"integer"},{"type":"null"}],"title":"Person Id","description":"Absent when the input entry didn't resolve to a person."},"sumble_url":{"anyOf":[{"type":"string","maxLength":2083,"minLength":1,"format":"uri"},{"type":"null"}],"title":"Sumble Url","description":"Link to the person's Sumble profile page."},"attributes":{"anyOf":[{"$ref":"#/components/schemas/PersonAttributes"},{"type":"null"}],"description":"The requested person-level attributes; absent when none resolve."},"related_people":{"anyOf":[{"$ref":"#/components/schemas/RelatedPeopleResult"},{"type":"null"}],"description":"Requested related people (match mode with `select.related_people`)."}},"type":"object","title":"PersonRow"},"PersonAttributes":{"properties":{"name":{"anyOf":[{"type":"string"},{"type":"null"}],"title":"Name"},"email":{"anyOf":[{"type":"string"},{"type":"null"}],"title":"Email","description":"Revealed work email (match mode only). The first successful reveal per person costs 10 credits; repeat reveals while active are free."},"phone":{"anyOf":[{"type":"string"},{"type":"null"}],"title":"Phone","description":"Revealed phone number (match mode only). The first successful reveal per person costs 80 credits; repeat reveals while active are free."},"linkedin_url":{"anyOf":[{"type":"string","maxLength":2083,"minLength":1,"format":"uri"},{"type":"null"}],"title":"Linkedin Url"},"job_title":{"anyOf":[{"type":"string"},{"type":"null"}],"title":"Job Title"},"job_function":{"anyOf":[{"type":"string"},{"type":"null"}],"title":"Job Function"},"job_level":{"anyOf":[{"type":"string"},{"type":"null"}],"title":"Job Level"},"location":{"anyOf":[{"type":"string"},{"type":"null"}],"title":"Location"},"country":{"anyOf":[{"type":"string"},{"type":"null"}],"title":"Country"},"current_employer":{"anyOf":[{"$ref":"#/components/schemas/CurrentEmployer"},{"type":"null"}]},"technologies":{"anyOf":[{"items":{"$ref":"#/components/schemas/PersonTechnology"},"type":"array"},{"type":"null"}],"title":"Technologies","description":"Technologies from the skills on the person's LinkedIn profile, normalized to Sumble's technology catalog. The slug can be used in `filter.query` (technology EQ '<slug>'). Empty when none of the profile's skills map to a known technology."},"person_score":{"anyOf":[{"$ref":"#/components/schemas/PersonScore"},{"type":"null"}],"description":"How well the person matches your ideal customer profile (ICP), 0-100, with a per-feature breakdown. Priced like other paid attributes. Filter mode only; selecting it explicitly requires the filter to resolve to exactly one organization and an ICP configured for your account (`attributes: \"all\"` skips it instead when unavailable). Included automatically when sorting with `order_by_column=\"person_score\"`."}},"type":"object","title":"PersonAttributes"},"CurrentEmployer":{"properties":{"organization_id":{"type":"integer","title":"Organization Id"},"name":{"anyOf":[{"type":"string"},{"type":"null"}],"title":"Name"},"start_date":{"anyOf":[{"type":"string"},{"type":"null"}],"title":"Start Date","description":"Start of the person's current role (YYYY-MM)."},"sumble_url":{"anyOf":[{"type":"string","maxLength":2083,"minLength":1,"format":"uri"},{"type":"null"}],"title":"Sumble Url","description":"Link to the organization's Sumble profile page."}},"type":"object","required":["organization_id"],"title":"CurrentEmployer"},"PersonTechnology":{"properties":{"name":{"type":"string","title":"Name"},"slug":{"type":"string","title":"Slug"}},"type":"object","required":["name","slug"],"title":"PersonTechnology"},"PersonScore":{"properties":{"value":{"type":"number","title":"Value","description":"How well the person matches your ideal customer profile (ICP), 0-100. Sum of the three contributions below."},"skill_contribution":{"type":"number","title":"Skill Contribution","description":"Points from technologies the person uses that match your ICP."},"job_function_contribution":{"type":"number","title":"Job Function Contribution","description":"Points from the person's job function matching your ICP."},"seniority_contribution":{"type":"number","title":"Seniority Contribution","description":"Points from the person's job level (seniority)."},"matched_features":{"items":{"$ref":"#/components/schemas/PersonScoreFeature"},"type":"array","title":"Matched Features","description":"The matched technologies and job functions behind the skill and job-function contributions, highest contribution first."}},"type":"object","required":["value","skill_contribution","job_function_contribution","seniority_contribution","matched_features"],"title":"PersonScore"},"PersonScoreFeature":{"properties":{"name":{"type":"string","title":"Name"},"slug":{"type":"string","title":"Slug"},"match_type":{"type":"string","enum":["technology","job_function"],"title":"Match Type"},"contribution":{"type":"number","title":"Contribution","description":"Points of the 0-100 score attributable to this feature."}},"type":"object","required":["name","slug","match_type","contribution"],"title":"PersonScoreFeature"},"RelatedPeopleResult":{"properties":{"managers":{"anyOf":[{"items":{"$ref":"#/components/schemas/RelatedPersonRow"},"type":"array"},{"type":"null"}],"title":"Managers","description":"Inferred managers; present only when requested."},"direct_reports":{"anyOf":[{"items":{"$ref":"#/components/schemas/RelatedPersonRow"},"type":"array"},{"type":"null"}],"title":"Direct Reports","description":"Inferred direct reports; present only when requested."}},"type":"object","title":"RelatedPeopleResult"},"RelatedPersonRow":{"properties":{"person_id":{"type":"integer","title":"Person Id"},"sumble_url":{"type":"string","maxLength":2083,"minLength":1,"format":"uri","title":"Sumble Url","description":"Link to the person's Sumble profile page."},"confidence":{"anyOf":[{"$ref":"#/components/schemas/RelatedPersonConfidence"},{"type":"null"}],"description":"Why this person was returned: confidence score (0-1) with per-category and per-feature breakdowns. Present when the `confidence` attribute is requested. Not the ICP-based `person_score`."},"attributes":{"anyOf":[{"$ref":"#/components/schemas/PersonAttributes"},{"type":"null"}],"description":"The requested person-level attributes; absent when none resolve."}},"type":"object","required":["person_id","sumble_url"],"title":"RelatedPersonRow"},"RelatedPersonConfidence":{"properties":{"score":{"type":"number","title":"Score","description":"How strongly this person relates to the source person (people endpoint) or job posting (jobs endpoint), 0-1. Sum of the five contributions below. Measures similarity on shared features (technologies, job functions, teams, locations, job titles) — unrelated to the ICP-based `person_score` attribute."},"technology_contribution":{"type":"number","title":"Technology Contribution","description":"Share from technologies shared with the source."},"job_function_contribution":{"type":"number","title":"Job Function Contribution","description":"Share from matching job functions."},"team_contribution":{"type":"number","title":"Team Contribution","description":"Share from shared teams."},"location_contribution":{"type":"number","title":"Location Contribution","description":"Share from matching locations."},"title_similarity_contribution":{"type":"number","title":"Title Similarity Contribution","description":"Share from semantic similarity between job titles."},"matched_features":{"items":{"$ref":"#/components/schemas/RelatedPersonConfidenceFeature"},"type":"array","title":"Matched Features","description":"The specific matched features behind the contributions, highest contribution first."}},"type":"object","required":["score","technology_contribution","job_function_contribution","team_contribution","location_contribution","title_similarity_contribution","matched_features"],"title":"RelatedPersonConfidence"},"RelatedPersonConfidenceFeature":{"properties":{"match_type":{"type":"string","enum":["technology","job_function","team","location","title_similarity"],"title":"Match Type"},"name":{"type":"string","title":"Name","description":"The matched feature's name; for title_similarity, the related person's own job title."},"slug":{"anyOf":[{"type":"string"},{"type":"null"}],"title":"Slug","description":"Null for location and title_similarity matches."},"contribution":{"type":"number","title":"Contribution","description":"Share of the confidence score attributable to this feature."}},"type":"object","required":["match_type","name","contribution"],"title":"RelatedPersonConfidenceFeature"},"HTTPValidationError":{"properties":{"detail":{"items":{"$ref":"#/components/schemas/ValidationError"},"type":"array","title":"Detail"}},"type":"object","title":"HTTPValidationError"},"ValidationError":{"properties":{"loc":{"items":{"anyOf":[{"type":"string"},{"type":"integer"}]},"type":"array","title":"Location"},"msg":{"type":"string","title":"Message"},"type":{"type":"string","title":"Error Type"},"input":{"title":"Input"},"ctx":{"type":"object","title":"Context"}},"type":"object","required":["loc","msg","type"],"title":"ValidationError"}}},"paths":{"/v9/people":{"post":{"tags":["people"],"summary":"Unified people endpoint","description":"Find, match, and enrich people. Provide a `people` list (list mode) or a `filter` (search mode) and compose the response with `select`.\n\nOn **v7 and earlier** this endpoint is synchronous: one call returns the people.\n\nOn **v8 and later** it is **asynchronous** in both modes. Send a `people` or `filter` body to start a background job — the response returns a `request_id` and `status: \"pending\"`, and no credits are charged yet (the request is validated and your balance pre-checked, so you still get a `400`/`402` up front). Then send `{ \"request_id\": \"...\" }` to the same endpoint to poll: you get `status` `pending`/`running` until it finishes, then `succeeded` (with the full `people`/`total` result) or `failed`. Credits are charged once, on the first `succeeded` poll; repeat polls report `credits_used: 0`. The async response adds `request_id` and `status` fields.","operationId":"enrich_people_unified__api_version__people_post","parameters":[],"requestBody":{"required":true,"content":{"application/json":{"schema":{"$ref":"#/components/schemas/EnrichPeopleRequest"}}}},"responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{"$ref":"#/components/schemas/EnrichPeopleResponse"}}}},"422":{"description":"Validation Error","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HTTPValidationError"}}}}}}}}}
```
