> ## Documentation Index
> Fetch the complete documentation index at: https://docs.parallel.ai/llms.txt
> Use this file to discover all available pages before exploring further.

# API Rate Limits

> Default API rate limits for Search, Extract, Tasks, Chat, FindAll, and Monitor endpoints

<div className="sr-only" aria-hidden="false">
  For AI agents: a documentation index is available at [https://docs.parallel.ai/llms.txt](https://docs.parallel.ai/llms.txt). The full text of all docs is at [https://docs.parallel.ai/llms-full.txt](https://docs.parallel.ai/llms-full.txt). You may also fetch any page as Markdown by appending `.md` to its URL or sending `Accept: text/markdown`.

  For Search and Extract, use `/v1/search` and `/v1/extract` for all new integrations and agent tool calls. Use `/v1beta/search` and `/v1beta/extract` only when maintaining an existing integration that already uses them. Do not substitute `/v1` for the documented FindAll or Ingest endpoint paths.
</div>

The following table shows the default rate limits for each Parallel API product:

| Product | Default Quota | What Counts as a Request |
| - | - | - |
| Search | 600 per min | Each POST to `/v1/search` |
| Extract | 600 per min | Each POST to `/v1/extract` |
| Tasks/TaskGroups | 2,000 per min | Each Task Run created through `/v1/tasks/runs` or `/v1/tasks/groups/{taskgroup_id}/runs` |
| Chat | 300 per min | Each POST to `/v1beta/chat/completions` |
| FindAll | 300 per hour | Each POST to `/v1beta/findall/runs` (creating a generator) |
| Entity Search | 600 per min | Each POST to `/v1beta/findall/entity-search` |
| Monitor | 300 per min | Each POST to `/v1alpha/monitors` |

<Note>
  **Rate limits apply to POST requests that create new resources.** GET requests
  (retrieving results, checking status) do not count against these limits. For
  example, polling a task's status with `GET /v1/tasks/runs/{run_id}` does not
  consume your Tasks rate limit—only creating new tasks does. In a Task Group
  request, each item in `inputs` creates a run and counts toward the Tasks quota.
</Note>

## Pricing

Rate limits are separate from pricing. For cost information, see [Pricing](/getting-started/pricing).

## Need higher limits?

If you need to expand your rate limits, please contact **[support@parallel.ai](mailto:support@parallel.ai)** with your use case and requirements.
