> For the complete documentation index, see [llms.txt](https://docs.sumble.com/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.sumble.com/api/core-data/organizations/organization-tech-stack.md).

# Organization tech stack

## Retrieve the confirmed-used tech stack for a single organization

Retrieve an organization's **confirmed-used** technology stack, grouped by business function and technology category — the same grouping and ranking used by the Tech Stack Overview page on sumble.com. Use this endpoint when you already have the Sumble organization `id` and want to know what an organization actually runs in production, not merely what its job posts or employee profiles mention in passing.

This is different from filtering the [Organizations](/api/core-data/organizations.md) endpoint by `technology`, or reading its `job_post_count` metric — both of those match on *any* mention. This endpoint restricts to technologies Sumble has confirmed are actually in use.

### Input

Pass the Sumble organization `id` in the URL path:

```bash
curl https://api.sumble.com/v9/organizations/1726684/techs \
  --header "Authorization: Bearer $API_KEY"
```

The endpoint does not accept a request body. If the organization does not exist, the API returns HTTP `404`.

To download the tech stack, pass a `file_name` ending in `.csv`, `.xlsx` for an Excel workbook, or `.json`:

```bash
curl "https://api.sumble.com/v9/organizations/1726684/techs?file_name=vanta-tech-stack.csv" \
  --header "Authorization: Bearer $API_KEY"
```

### Response

The response contains a `business_functions` array in the same display order as the Tech Stack Overview page. Each business function contains up to five categories, ranked by confirmed-use evidence. Business functions without detected technologies are returned with an empty `categories` array.

Each category includes a `category_slug`, `category_name` (short), and `category_name_full`, plus:

* **`technologies`** — the confirmed-used, top-tier ("primary") technologies Sumble found for that category at this organization. Each technology includes its `name`, `slug`, a `job_post_used_count` (the number of job posts confirming active use, not mere mentions), and its `domain` when known.
* **`evidence`** — the full ranked list of every confirmed-used technology detected in that category (a superset of `technologies`), each as `{name, job_post_used_count}`. Use this for the complete picture rather than just the headline picks.

Categories without confirmed-used technologies are omitted.

### File output

A `.json` file holds the `business_functions` array in the same structure as the inline response, with each function's categories, technologies, and evidence nested. Its `row_count` counts business functions, and `columns` lists their top-level keys.

A `.csv` or `.xlsx` file has one row per technology in a category's full `evidence` list. Its width is fixed. Organizations with more technologies produce more rows, not more columns.

Each row repeats its organization, business function, and category context. `business_function_rank`, `category_rank`, and `evidence_rank` preserve the response order. `is_primary` marks technologies that also appear in the category's `technologies` list. Those rows include `technology_slug`, `technology_domain`, and `primary_rank`; the fields are blank for evidence-only technologies.

When `file_name` is present, `business_functions` is empty in the JSON response and `file` contains the filename, columns, row count, size, and authenticated download URL. The credit cost does not change.

### Credit cost

1 credit per technology returned in `technologies` (summed across all returned business functions and categories). Organizations with no confirmed-used technologies do not consume credits.

## GET /v9/organizations/{organization\_id}/techs

> Get organization tech stack

```json
{"openapi":"3.1.0","info":{"title":"Sumble API","version":"v9"},"servers":[{"url":"https://api.sumble.com"}],"security":[{"api_token":[]}],"components":{"securitySchemes":{"api_token":{"type":"http","scheme":"bearer"}},"schemas":{"OrganizationTechStackResponse":{"properties":{"id":{"type":"string","format":"uuid","title":"Id"},"credits_used":{"type":"integer","title":"Credits Used"},"credits_remaining":{"type":"integer","title":"Credits Remaining"},"organization_id":{"type":"integer","title":"Organization Id"},"organization_slug":{"type":"string","title":"Organization Slug"},"business_functions":{"items":{"$ref":"#/components/schemas/OrganizationTechStackBusinessFunction"},"type":"array","title":"Business Functions"}},"type":"object","required":["id","credits_used","credits_remaining","organization_id","organization_slug","business_functions"],"title":"OrganizationTechStackResponse"},"OrganizationTechStackBusinessFunction":{"properties":{"business_function":{"type":"string","title":"Business Function"},"categories":{"items":{"$ref":"#/components/schemas/OrganizationTechStackCategory"},"type":"array","title":"Categories"}},"type":"object","required":["business_function","categories"],"title":"OrganizationTechStackBusinessFunction"},"OrganizationTechStackCategory":{"properties":{"category_slug":{"type":"string","title":"Category Slug"},"category_name":{"type":"string","title":"Category Name"},"category_name_full":{"type":"string","title":"Category Name Full"},"technologies":{"items":{"$ref":"#/components/schemas/OrganizationTechStackTechnology"},"type":"array","title":"Technologies"},"evidence":{"items":{"$ref":"#/components/schemas/OrganizationTechStackEvidenceItem"},"type":"array","title":"Evidence"}},"type":"object","required":["category_slug","category_name","category_name_full","technologies","evidence"],"title":"OrganizationTechStackCategory"},"OrganizationTechStackTechnology":{"properties":{"name":{"type":"string","title":"Name"},"slug":{"type":"string","title":"Slug"},"job_post_used_count":{"type":"integer","title":"Job Post Used Count"},"domain":{"anyOf":[{"type":"string"},{"type":"null"}],"title":"Domain"}},"type":"object","required":["name","slug","job_post_used_count"],"title":"OrganizationTechStackTechnology","description":"A confirmed-used (\"primary\") technology within a category."},"OrganizationTechStackEvidenceItem":{"properties":{"name":{"type":"string","title":"Name"},"job_post_used_count":{"type":"integer","title":"Job Post Used Count"}},"type":"object","required":["name","job_post_used_count"],"title":"OrganizationTechStackEvidenceItem","description":"One confirmed-used technology detected in a category, with the number\nof job posts that confirm it (not merely mention it) is actually in use.\n\nThis is the full, unfiltered list for the category — a superset of\n`OrganizationTechStackCategory.technologies`, which is trimmed to the\ntop-tier (primary) picks."},"HTTPValidationError":{"properties":{"detail":{"items":{"$ref":"#/components/schemas/ValidationError"},"type":"array","title":"Detail"}},"type":"object","title":"HTTPValidationError"},"ValidationError":{"properties":{"loc":{"items":{"anyOf":[{"type":"string"},{"type":"integer"}]},"type":"array","title":"Location"},"msg":{"type":"string","title":"Message"},"type":{"type":"string","title":"Error Type"},"input":{"title":"Input"},"ctx":{"type":"object","title":"Context"}},"type":"object","required":["loc","msg","type"],"title":"ValidationError"}}},"paths":{"/v9/organizations/{organization_id}/techs":{"get":{"tags":["organizations"],"summary":"Get organization tech stack","operationId":"get_organization_techs__api_version__organizations__organization_id__techs_get","parameters":[{"name":"organization_id","in":"path","required":true,"schema":{"type":"integer","title":"Organization Id"}}],"responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{"$ref":"#/components/schemas/OrganizationTechStackResponse"}}}},"422":{"description":"Validation Error","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HTTPValidationError"}}}}}}}}}
```
