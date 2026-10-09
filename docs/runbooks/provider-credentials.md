# Runbook: provider API credentials

Provider credentials are read by the backend connector runtime. Never put them in the web
application, a browser request, source control, an issue, or chat.

## Local development

Create the ignored `.env` with the variables in docs/ENVIRONMENT.md, keep `CONNECTORS=on`, then fill only the credentials for the
providers you use. `make run` and the local Compose API load `.env`; restart the API after changing
credentials. No provider requests are made until a run invokes a provider operation.

| Provider | Local environment variable | Cloud Secret Manager secret |
|---|---|---|
| Parallel | `P_SEARCH_API_KEY` | `p-search-api-key` |
| Firecrawl | `P_SCRAPE_API_KEY` | `p-scrape-api-key` |
| PredictLeads | `P_SIGNALS_API_KEY` and `P_SIGNALS_API_TOKEN` | `p-signals-api-key` and `p-signals-api-token` |
| BitScale | `P_PEOPLE_API_KEY` | `p-people-api-key` |
| Sumble | `P_ORG_API_KEY` | `p-org-api-key` |

Missing credentials do not use placeholders: the corresponding provider call fails with an
authentication error. Existing calls remain server-side and are routed through the connector
runtime's timeouts, rate limits, retry policy, cost reservation, and audit ledger.

## Cloud Run

Terraform has already provisioned the Secret Manager names and maps them to the same environment
variables on the API and worker. Add secret versions using the existing
[secrets runbook](secrets.md), then restart or redeploy the services so they load the latest
version. Do not put secret values in Terraform variables or state.

## Provider operation status

Parallel, Firecrawl, PredictLeads, and Sumble have implemented backend operations. BitScale
credentials alone do not enable its people operations: the adapter needs the workspace-specific
grid contract before it can safely interpret grid outputs. To complete that adapter, provide the
grid ID for each operation, the output of `GET /api/v1/grids/:gridId/curl`, and two or three
completed run response examples with personal data redacted but field names and value types kept.
The grid request appends a row in the provider workspace, so use a test grid for any sample runs.
See [the BitScale connector notes](../providers/p_people.md) for the fields needed.

Provider plans and credits vary. Confirm each account's plan and usage limits before enabling
production runs; unknown call costs count against the configured run budget reserve.
