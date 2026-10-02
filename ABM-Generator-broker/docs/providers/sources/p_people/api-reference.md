> ## Documentation Index
> Fetch the complete documentation index at: https://docs.bitscale.ai/llms.txt
> Use this file to discover all available pages before exploring further.

# Bitscale API Reference

**Version:** v1 · **Plan Required:** Enterprise

| | |
| - | - |
| **Base URL** | `https://api.bitscale.ai/api/v1` |
| **Authentication** | `X-API-KEY: <your-api-key>` |
| **Content-Type** | `application/json` |

> **Enterprise Only** — BitScale APIs is an Enterprise feature. Your workspace plan must be upgraded before any of these endpoints will respond successfully.

***

## Table of Contents

1. [Overview](#1-overview)
2. [Authentication](#2-authentication)
3. [Rate Limits](#3-rate-limits)
4. [Error Format](#4-error-format)
5. [Endpoints](#5-endpoints)
   * [5.1 GET /workspace — Get Workspace Details](#51-get-workspace-details)
   * [5.2 GET /grids — List Grids in Workspace](#52-list-grids-in-workspace)
   * [5.3 GET /grids/:gridId — Get Grid Details](#53-get-grid-details)
   * [5.4 GET /grids/:gridId/curl — Get Grid Curl Command](#54-get-grid-curl-command)
   * [5.5 POST /grids/:gridId/run — Run a Grid](#55-run-a-grid)
   * [5.6 GET /run/status/:requestId — Get Run Status](#56-get-run-status)
   * [5.7 POST /api-key/rotate — Rotate API Key](#57-rotate-api-key)
6. [Sync vs Async Modes](#6-sync-vs-async-modes)
7. [Error Code Reference](#7-error-code-reference)

***

## 1. Overview

The BitScale External API lets you programmatically query workspace and grid metadata, trigger grid runs, poll for results, and manage your workspace API key. It is designed for use with MCP integrations, CI/CD pipelines, and any external system that needs to interact with BitScale grids automatically.

### Endpoints at a Glance

| Method | Path | Description |
| - | - | - |
| `GET` | `/workspace` | Get workspace details (plan, credits, members) |
| `GET` | `/grids` | List all grids in the workspace (paginated) |
| `GET` | `/grids/:gridId` | Get full metadata for a specific grid |
| `GET` | `/grids/:gridId/curl` | Get a ready-to-use curl command and API contract for running a grid |
| `POST` | `/grids/:gridId/run` | Trigger a grid run (sync or async) |
| `GET` | `/run/status/:requestId` | Poll run status and retrieve outputs |
| `POST` | `/api-key/rotate` | Generate a new workspace API key |

***

## 2. Authentication

All requests must include a workspace-level API key in the `X-API-KEY` header. The key uniquely identifies your workspace — no separate workspace ID header is required.

### Request Header

| Header | Value |
| - | - |
| `X-API-KEY` | Your workspace API key (required) |

### Example

```bash theme={null}
curl -X POST "https://api.bitscale.ai/api/v1/grids/GRID_ID/run" \
  -H "X-API-KEY: sk-live-abc123xyz" \
  -H "Content-Type: application/json" \
  -d '{ "mode": "sync", "inputs": { "company_name": "Acme Corp" } }'
```

### Obtaining Your API Key

1. Log into your BitScale workspace.
2. Navigate to **Settings → Accounts → API Keys**.
3. Copy the displayed workspace API key.
4. To generate a new key, use [`POST /api/v1/api-key/rotate`](#57-rotate-api-key) or rotate it from the dashboard.

> **⚠ Important** — Your API key identifies your entire workspace. Keep it secret and never expose it in client-side code or public repositories. If compromised, rotate it immediately.

### Authentication Errors

| HTTP | Code | Description |
| - | - | - |
| `401` | `INVALID_API_KEY` | Missing `X-API-KEY` header, or the key is invalid / expired. |

***

## 3. Rate Limits

The BitScale External API enforces a default rate limit of **5 requests per second** per workspace. Rate limiting is applied globally across all endpoints — including read-only endpoints like `GET /workspace` and `GET /grids`.

| Limit | Default | Scope |
| - | - | - |
| Requests/sec | 5 | Per workspace |

Exceeding this limit will result in a `429 Too Many Requests` response. If your integration requires a higher (or lower) limit, reach out to your account manager or contact support at [team@bitscale.ai](mailto:team@bitscale.ai) to have it adjusted.

> **ℹ Note** — Rate limits apply across all endpoints within a workspace. Polling `GET /run/status/:requestId` counts toward your limit, so poll at a reasonable interval (every 2–5 seconds) rather than as fast as possible.

### Rate Limit Response Headers

Every API response includes the following headers so you can track your usage:

| Header | Description |
| - | - |
| `X-RateLimit-Limit` | Maximum number of requests allowed per second for your workspace. |
| `X-RateLimit-Remaining` | Number of requests remaining in the current 1-second window. |
| `Retry-After` | Seconds to wait before retrying. Present only on `429` responses (`1`). |

### Example — Rate Limit Exceeded

```text theme={null}
HTTP/1.1 429 Too Many Requests
X-RateLimit-Limit: 5
X-RateLimit-Remaining: 0
Retry-After: 1

{
  "error": {
    "code":    "RATE_LIMIT_EXCEEDED",
    "message": "Bitscale external API rate limit exceeded (5 requests/second per workspace)."
  }
}
```

***

## 4. Error Format

When a request fails, the API returns a JSON object with a top-level `error` key containing a machine-readable `code` and a human-readable `message`.

```json theme={null}
{
  "error": {
    "code":    "ERROR_CODE_STRING",
    "message": "Human readable description of the error."
  }
}
```

### Example — Missing API Key

```text theme={null}
HTTP/1.1 401 Unauthorized

{
  "error": {
    "code":    "INVALID_API_KEY",
    "message": "Missing or invalid X-API-KEY header"
  }
}
```

***

## 5. Endpoints

***

### 5.1 Get Workspace Details

```text theme={null}
GET /api/v1/workspace
```

Returns details about the workspace associated with the provided `X-API-KEY`, including plan information, credit balances, and member counts.

#### Request

No request body or path parameters required. The workspace is identified from the `X-API-KEY` header.

```text theme={null}
GET /api/v1/workspace
X-API-KEY: sk-live-abc123xyz
```

#### Response — 200 OK

```json theme={null}
{
  "id": "ws-a1b2c3d4",
  "name": "Acme Corp Workspace",
  "created_at": "2024-06-01T10:00:00Z",
  "created_by": {
    "id": "user-uuid-here",
    "name": "Jane Smith",
    "email": "jane@acme.com"
  },
  "plan": {
    "name": "Enterprise",
    "credits_included": 50000,
    "billing_interval": "monthly",
    "next_billing_date": "2026-04-01T00:00:00Z",
    "price": 499.00,
    "currency": "USD"
  },
  "credits": {
    "total": 52000,
    "used": 12400,
    "remaining": 39600,
    "plan_credits": 50000,
    "rollover": 2000,
    "topup": 0
  },
  "people_company_searches": {
    "limit": 1000,
    "used": 230,
    "remaining": 770,
    "next_renewal_date": "2026-04-11T00:00:00Z"
  },
  "members": {
    "total": 8,
    "owners": 1,
    "admins": 2,
    "editors": 5
  }
}
```

#### Response Fields

| Field | Type | Description |
| - | - | - |
| `id` | `string` | Unique workspace identifier. |
| `name` | `string` | Display name of the workspace. |
| `created_at` | `string` | ISO 8601 timestamp of workspace creation. |
| `created_by` | `object` | Details of the user who created the workspace. |
| `plan.name` | `string` | Current plan name (e.g. `"Enterprise"`). |
| `plan.credits_included` | `integer` | Credits included in the current billing cycle. |
| `plan.billing_interval` | `string` | `"monthly"`, `"yearly"`, or `"quarterly"`. |
| `plan.next_billing_date` | `string` | ISO 8601 timestamp of the next billing date. `null` if not applicable. |
| `plan.price` | `number` | Plan price. `null` if not applicable. |
| `plan.currency` | `string` | Currency code (e.g. `"USD"`). `null` if not applicable. |
| `credits.total` | `integer` | Total credits available (plan + rollover + topup). |
| `credits.used` | `integer` | Credits consumed in the current period. |
| `credits.remaining` | `integer` | Credits still available. |
| `credits.plan_credits` | `integer` | Credits from the base plan. |
| `credits.rollover` | `integer` | Rollover credits carried over from the previous period. |
| `credits.topup` | `integer` | Credits added via top-up purchases. |
| `people_company_searches.limit` | `integer` | Monthly limit for People/Company search enrichments. |
| `people_company_searches.used` | `integer` | Searches used in the current month. |
| `people_company_searches.remaining` | `integer` | Searches remaining this month. |
| `people_company_searches.next_renewal_date` | `string` | ISO 8601 date when the monthly search limit resets. |
| `members.total` | `integer` | Total number of workspace members. |
| `members.owners` | `integer` | Number of members with the Owner role. |
| `members.admins` | `integer` | Number of members with the Admin role. |
| `members.editors` | `integer` | Number of members with the Editor role. |

#### Response Status Codes

| HTTP | Code | Description |
| - | - | - |
| `200` | — | Workspace details returned successfully. |
| `401` | `INVALID_API_KEY` | `X-API-KEY` header is missing or invalid. |
| `404` | `WORKSPACE_NOT_FOUND` | Workspace not found for the provided API key. |
| `429` | `RATE_LIMIT_EXCEEDED` | Too many requests. Default limit is 5 req/sec. |

***

### 5.2 List Grids in Workspace

```text theme={null}
GET /api/v1/grids
```

Returns a paginated list of grids in the workspace identified by the `X-API-KEY`, along with their column definitions (runnable columns only). Supports filtering by name.

#### Query Parameters

| Parameter | Type | Required | Default | Description |
| - | - | - | - | - |
| `search` | `string` | No | — | Filter grids by name (case-insensitive substring match). |
| `page` | `integer` | No | `1` | Page number (1-based). |
| `limit` | `integer` | No | `20` | Number of results per page. Maximum `100`. |

#### Example Request

```text theme={null}
GET /api/v1/grids?search=leads&page=1&limit=20
X-API-KEY: sk-live-abc123xyz
```

#### Response — 200 OK

```json theme={null}
{
  "grids": [
    {
      "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "name": "Lead Enrichment Grid",
      "description": "Enriches inbound leads with firmographic data.",
      "row_count": 1540,
      "column_count": 8,
      "created_at": "2025-11-01T09:00:00Z",
      "updated_at": "2026-03-12T14:22:00Z",
      "columns": [
        {
          "id": "col-key-abc",
          "name": "Company Description",
          "type": "enrichment",
          "dependencies": []
        },
        {
          "id": "col-key-def",
          "name": "Funding Summary",
          "type": "formula",
          "dependencies": ["col-key-abc"]
        }
      ]
    }
  ],
  "total": 42,
  "page": 1,
  "limit": 20,
  "total_pages": 3
}
```

#### Response Fields

| Field | Type | Description |
| - | - | - |
| `grids` | `array` | List of grid objects for the current page. |
| `total` | `integer` | Total number of grids matching the query. |
| `page` | `integer` | Current page number. |
| `limit` | `integer` | Number of results per page. |
| `total_pages` | `integer` | Total number of pages. |

#### Grid Object Fields

| Field | Type | Description |
| - | - | - |
| `id` | `string` | UUID of the grid. |
| `name` | `string` | Display name of the grid. |
| `description` | `string` | Grid description. May be empty. |
| `row_count` | `integer` | Number of rows currently in the grid. |
| `column_count` | `integer` | Total number of columns. |
| `created_at` | `string` | ISO 8601 creation timestamp. |
| `updated_at` | `string` | ISO 8601 last-updated timestamp. |
| `columns` | `array` | Runnable columns only (type: `enrichment`, `formula`, or `merge`). See below. |

#### Column Object Fields (`columns` array)

| Field | Type | Description |
| - | - | - |
| `id` | `string` | Column key used as the identifier when triggering runs. |
| `name` | `string` | Human-readable display name of the column. |
| `type` | `string` | Column type: `"enrichment"`, `"formula"`, or `"merge"`. |
| `dependencies` | `string[]` | Array of column key IDs that this column depends on. Empty array if no dependencies. |

#### Response Status Codes

| HTTP | Code | Description |
| - | - | - |
| `200` | — | Grid list returned successfully. |
| `401` | `INVALID_API_KEY` | `X-API-KEY` header is missing or invalid. |
| `429` | `RATE_LIMIT_EXCEEDED` | Too many requests. Default limit is 5 req/sec. |
| `500` | `INTERNAL_ERROR` | Unexpected server error. |

***

### 5.3 Get Grid Details

```text theme={null}
GET /api/v1/grids/:gridId
```

Returns full metadata for a single grid, including all column definitions, data sources, and grid settings.

#### Path Parameters

| Parameter | Description |
| - | - |
| `gridId` | UUID of the grid. Found in the grid URL: `app.bitscale.ai/grid/{gridId}` |

#### Example Request

```text theme={null}
GET /api/v1/grids/a1b2c3d4-e5f6-7890-abcd-ef1234567890
X-API-KEY: sk-live-abc123xyz
```

#### Response — 200 OK

```json theme={null}
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "name": "Lead Enrichment Grid",
  "description": "Enriches inbound leads with firmographic data.",
  "row_count": 1540,
  "created_at": "2025-11-01T09:00:00Z",
  "updated_at": "2026-03-12T14:22:00Z",
  "settings": {
    "auto_run": false,
    "auto_dedupe": true,
    "visibility": "workspace",
    "dedupe_column_id": "col-key-abc"
  },
  "columns": [
    {
      "id": "col-key-abc",
      "name": "Company Name",
      "type": "text"
    },
    {
      "id": "col-key-def",
      "name": "Company Description",
      "type": "enrichment"
    }
  ],
  "sources": [
    {
      "id": "src-uuid-123",
      "display_name": "BitScale API",
      "type": "api",
      "is_scheduled": false,
      "status": "active",
      "last_run_at": "2026-03-12T14:20:00Z",
      "next_run_at": null,
      "schedule": null
    }
  ]
}
```

#### Response Fields

| Field | Type | Description |
| - | - | - |
| `id` | `string` | UUID of the grid. |
| `name` | `string` | Display name of the grid. |
| `description` | `string` | Grid description. May be empty. |
| `row_count` | `integer` | Number of rows in the grid. |
| `created_at` | `string` | ISO 8601 creation timestamp. |
| `updated_at` | `string` | ISO 8601 last-updated timestamp. |
| `settings` | `object` | Grid-level settings (see below). |
| `columns` | `array` | All column definitions (see below). |
| `sources` | `array` | Data sources attached to the grid (see below). |

#### Settings Object

| Field | Type | Description |
| - | - | - |
| `auto_run` | `boolean` | Whether new rows trigger automatic enrichment. |
| `auto_dedupe` | `boolean` | Whether duplicate rows are automatically removed. |
| `visibility` | `string` | Grid visibility: `"workspace"` or `"private"`. |
| `dedupe_column_id` | `string` | Column key used for deduplication. Present only when `auto_dedupe` is `true`. |

#### Column Object

| Field | Type | Description |
| - | - | - |
| `id` | `string` | Column key identifier. |
| `name` | `string` | Human-readable display name. |
| `type` | `string` | Column type: `"text"`, `"enrichment"`, `"formula"`, or `"merge"`. |

#### Source Object

| Field | Type | Description |
| - | - | - |
| `id` | `string` | UUID of the data source. |
| `display_name` | `string` | Display name of the source. |
| `type` | `string` | Source type (e.g. `"api"`, `"csv"`, etc.). |
| `is_scheduled` | `boolean` | Whether the source runs on a schedule. |
| `status` | `string` | Current source status (e.g. `"active"`, `"paused"`). |
| `last_run_at` | `string` | ISO 8601 timestamp of last run. `null` if never run. |
| `next_run_at` | `string` | ISO 8601 timestamp of next scheduled run. `null` if not scheduled. |
| `schedule` | `object` | Schedule details. Present only when `is_scheduled` is `true` (see below). |

#### Schedule Object (when `is_scheduled: true`)

| Field | Type | Description |
| - | - | - |
| `cron_schedule` | `string` | Cron expression for the schedule. |
| `next_run_at` | `string` | ISO 8601 timestamp of the next scheduled run. |

#### Response Status Codes

| HTTP | Code | Description |
| - | - | - |
| `200` | — | Grid details returned successfully. |
| `401` | `INVALID_API_KEY` | `X-API-KEY` header is missing or invalid. |
| `403` | `GRID_NOT_FOUND` | Grid exists but does not belong to your workspace. |
| `404` | `GRID_NOT_FOUND` | No grid found for the provided `gridId`. |
| `429` | `RATE_LIMIT_EXCEEDED` | Too many requests. Default limit is 5 req/sec. |
| `500` | `INTERNAL_ERROR` | Unexpected server error. |

***

### 5.4 Get Grid Curl Command

```text theme={null}
GET /api/v1/grids/:gridId/curl
```

Returns a ready-to-use curl command and a structured API contract for running a specific grid. This endpoint is designed for AI agents and integrations that need to understand what inputs a grid requires and how to call the run endpoint — without any trial and error.

The response includes the derived required inputs (traced from the grid's column dependencies), the full run URL, request body shape, and a copy-paste-ready curl command.

> **💡 Tip for AI agents** — Call this endpoint first to discover the exact input fields needed for a grid, then use `POST /grids/:gridId/run` with the returned `request_body.inputs` shape.

#### Path Parameters

| Parameter | Description |
| - | - |
| `gridId` | UUID of the grid. Found in the grid URL: `app.bitscale.ai/grid/{gridId}` |

#### Query Parameters

| Parameter | Type | Required | Description |
| - | - | - | - |
| `output_columns` | `string` | No | Comma-separated list of column key IDs to include in the run. When provided, required inputs are derived only from the dependencies of the specified columns. When omitted, inputs are derived from all runnable columns in the grid. |

#### Example Request — All Columns

```text theme={null}
GET /api/v1/grids/a1b2c3d4-e5f6-7890-abcd-ef1234567890/curl
X-API-KEY: sk-live-abc123xyz
```

#### Example Request — Specific Output Columns

```text theme={null}
GET /api/v1/grids/a1b2c3d4-e5f6-7890-abcd-ef1234567890/curl?output_columns=col-key-def,col-key-ghi
X-API-KEY: sk-live-abc123xyz
```

#### Response — 200 OK

```json theme={null}
{
  "grid_id":   "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "grid_name": "Lead Enrichment Grid",
  "run_url":   "https://api.bitscale.ai/api/v1/grids/a1b2c3d4-e5f6-7890-abcd-ef1234567890/run",
  "method":    "POST",
  "headers": {
    "Content-Type": "application/json",
    "X-API-KEY":    "YOUR_WORKSPACE_API_KEY"
  },
  "request_body": {
    "mode": "sync",
    "inputs": {
      "company_name": "value",
      "website":      "value"
    },
    "output_columns": [
      "col-key-def",
      "col-key-ghi"
    ]
  },
  "output_columns": [
    {
      "id":   "col-key-def",
      "name": "Company Description",
      "type": "enrichment"
    },
    {
      "id":   "col-key-ghi",
      "name": "Funding Stage",
      "type": "enrichment"
    }
  ],
  "curl": "curl -X POST 'https://api.bitscale.ai/api/v1/grids/a1b2c3d4-e5f6-7890-abcd-ef1234567890/run' \\\n  -H 'X-API-KEY: YOUR_WORKSPACE_API_KEY' \\\n  -H 'Content-Type: application/json' \\\n  -d '{\n  \"inputs\": {\n    \"company_name\": \"value\",\n    \"website\": \"value\"\n  },\n  \"mode\": \"sync\",\n  \"output_columns\": [\n    \"col-key-def\",\n    \"col-key-ghi\"\n  ]\n}'"
}
```

#### Response Fields

| Field | Type | Description |
| - | - | - |
| `grid_id` | `string` | UUID of the grid. |
| `grid_name` | `string` | Display name of the grid. |
| `run_url` | `string` | Full URL for the `POST /run` endpoint for this grid. |
| `method` | `string` | HTTP method to use — always `"POST"`. |
| `headers` | `object` | Required request headers. Replace `YOUR_WORKSPACE_API_KEY` with your actual key. |
| `request_body` | `object` | The full request body to send to the run endpoint. The `inputs` keys are derived from the grid's column dependencies. |
| `output_columns` | `array` | Details of the output columns explicitly requested via `output_columns`. Omitted if no `output_columns` param was given. |
| `curl` | `string` | A ready-to-use shell curl command. Replace `YOUR_WORKSPACE_API_KEY` before executing. |

#### `request_body` Object

| Field | Type | Description |
| - | - | - |
| `mode` | `string` | Always `"sync"` in the generated contract. Change to `"async"` for long-running grids if needed. |
| `inputs` | `object` | Key-value map of required input fields with placeholder `"value"` strings. Replace with real data. |
| `output_columns` | `string[]` | Column key IDs to return. Present only when `output_columns` was specified in the query param. |

#### `output_columns` Array Item

| Field | Type | Description |
| - | - | - |
| `id` | `string` | Column key ID. |
| `name` | `string` | Human-readable display name. |
| `type` | `string` | Column type: `"enrichment"`, `"formula"`, or `"merge"`. |

#### Response Status Codes

| HTTP | Code | Description |
| - | - | - |
| `200` | — | Curl command and API contract returned successfully. |
| `400` | `MISSING_SOURCE` | Grid has no BitScale API source. Add one in grid settings first. |
| `400` | `INVALID_OUTPUT_COLUMN` | One of the specified `output_columns` keys does not exist in the grid. |
| `401` | `INVALID_API_KEY` | `X-API-KEY` header is missing or invalid. |
| `403` | `GRID_NOT_FOUND` | Grid exists but does not belong to your workspace. |
| `404` | `GRID_NOT_FOUND` | No grid found for the provided `gridId`. |
| `429` | `RATE_LIMIT_EXCEEDED` | Too many requests. Default limit is 5 req/sec. |

***

### 5.5 Run a Grid

```text theme={null}
POST /api/v1/grids/:gridId/run
```

Appends a new row to the specified grid using the provided inputs and triggers all column enrichments. Supports two execution modes:

* **`sync` (default)** — waits up to 120 seconds for completion and returns outputs directly. If still processing at the deadline, returns a `request_id` to poll.
* **`async`** — returns a `request_id` immediately. Poll `GET /run/status/:requestId` for results.

> **💡 Get your curl instantly** — Use [`GET /grids/:gridId/curl`](#54-get-grid-curl-command) to retrieve a pre-built curl command and the exact input fields required for any grid. You can also open any grid in [app.bitscale.ai](https://app.bitscale.ai), click on the **Data source** column, select the BitScale API source, and find a ready-to-use curl command pre-filled with your grid ID, API key, and input fields. Use the output column toggles to include or exclude specific columns — the curl updates live to reflect your selection.

#### Path Parameters

| Parameter | Description |
| - | - |
| `gridId` | UUID of the grid to run. Found in the grid URL: `app.bitscale.ai/grid/{gridId}` |

#### Request Headers

| Header | Value |
| - | - |
| `X-API-KEY` | Your workspace API key (required) |
| `Content-Type` | `application/json` (required) |

#### Request Body

| Field | Type | Required | Description |
| - | - | - | - |
| `mode` | `string` | No | `"sync"` or `"async"`. Defaults to `"sync"` if omitted. |
| `inputs` | `object` | **Yes** | Key-value map of column keys (e.g. `"company_name"`) to their input values. Use the column key shown in the API Datasource panel — not a UUID. |
| `output_columns` | `string[]` | No | Array of column UUID keys to include in the response. If omitted, all enriched columns are returned. |
| `source_id` | `string` | No | UUID of the BitScale API source on the grid. If omitted, the first available source is used automatically. |
| `webhook_url` | `string` | No | *(async only)* HTTPS callback URL for when the run completes. Reserved for future use. |

#### Example — Sync Mode

```json theme={null}
POST /api/v1/grids/a1b2c3d4-e5f6-7890-abcd-ef1234567890/run
X-API-KEY: sk-live-abc123xyz
Content-Type: application/json

{
  "mode": "sync",
  "inputs": {
    "company_name": "Acme Corp",
    "website":      "acme.com"
  },
  "output_columns": [
    "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
    "6ba7b811-9dad-11d1-80b4-00c04fd430c8"
  ]
}
```

#### Example — Async Mode

```json theme={null}
POST /api/v1/grids/a1b2c3d4-e5f6-7890-abcd-ef1234567890/run
X-API-KEY: sk-live-abc123xyz
Content-Type: application/json

{
  "mode": "async",
  "inputs": {
    "company_name": "Acme Corp"
  }
}
```

#### Responses

**200 OK — Sync Completed** *(run finished within 120 seconds)*

```json theme={null}
{
  "mode":   "sync",
  "status": "completed",
  "outputs": {
    "6ba7b810-9dad-11d1-80b4-00c04fd430c8": {
      "value": "AI-powered data enrichment platform",
      "name":  "Company Description"
    },
    "6ba7b811-9dad-11d1-80b4-00c04fd430c8": {
      "value": "Series B",
      "name":  "Funding Stage"
    }
  }
}
```

**200 OK — Async Started / Sync Timeout** *(poll `request_id` for results)*

```json theme={null}
{
  "mode":       "async",
  "status":     "running",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "poll_url":   "https://api.bitscale.ai/api/v1/run/status/550e8400-e29b-41d4-a716-446655440000",
  "message":    "Row is still running. Poll the poll_url for results."
}
```

#### Output Object Shape

Each key in `outputs` is a column UUID. The value is an object with:

| Field | Description |
| - | - |
| `value` | The enriched output (string, number, array, or object depending on column type) |
| `name` | Human-readable display name of the column as set in the grid |

#### Response Status Codes

| HTTP | Code | Description |
| - | - | - |
| `200` | — | Request accepted. Check `status` field in body. |
| `400` | `MISSING_REQUIRED_FIELD` | `inputs` is missing or request body is malformed JSON. |
| `400` | `MISSING_SOURCE` | Grid has no BitScale API source. Add one in grid settings. |
| `400` | `INVALID_SOURCE` | The `source_id` provided does not exist for this grid. |
| `400` | `INVALID_REQUEST` | `mode` is not `"sync"` or `"async"`. |
| `401` | `INVALID_API_KEY` | `X-API-KEY` header is missing or invalid. |
| `403` | `FEATURE_DISABLED` | BitScale APIs not enabled on your plan. Upgrade to Enterprise. |
| `403` | `GRID_NOT_FOUND` | Grid exists but does not belong to your workspace. |
| `404` | `GRID_NOT_FOUND` | No grid found for the provided `gridId`. |
| `429` | `RATE_LIMIT_EXCEEDED` | Too many requests. Default limit is 5 req/sec per workspace. |
| `500` | `RUN_FAILED` | Row run encountered an error. Check `message` for details. |
| `500` | `INTERNAL_ERROR` | Unexpected server error. Contact support if this persists. |

***

### 5.6 Get Run Status

```text theme={null}
GET /api/v1/run/status/:requestId
```

Returns the current status of a run identified by its `request_id`. Call this after receiving a `request_id` from `POST /grids/:gridId/run` — either in async mode or when sync mode times out.

> **ℹ Note** — Poll every 2–5 seconds until `status` is `"completed"` or `"failed"`. Avoid polling more frequently as requests count toward your rate limit.

#### Path Parameters

| Parameter | Description |
| - | - |
| `requestId` | UUID returned as `request_id` from `POST /grids/:gridId/run` |

#### Example Request

```text theme={null}
GET /api/v1/run/status/550e8400-e29b-41d4-a716-446655440000
X-API-KEY: sk-live-abc123xyz
```

#### Responses

**200 OK — Still Running**

```json theme={null}
{
  "mode":    "async",
  "status":  "running",
  "grid_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

**200 OK — Completed**

```json theme={null}
{
  "mode":    "async",
  "status":  "completed",
  "grid_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "outputs": {
    "6ba7b810-9dad-11d1-80b4-00c04fd430c8": {
      "value": "AI-powered data enrichment platform",
      "name":  "Company Description"
    }
  }
}
```

#### Response Fields

| Field | Type | Description |
| - | - | - |
| `mode` | `string` | `"sync"` or `"async"` — matches the mode used when the run was started. |
| `status` | `string` | `"running"`, `"completed"`, or `"failed"`. |
| `grid_id` | `string` | UUID of the grid that was run. |
| `outputs` | `object` | Present only when `status` is `"completed"`. Same shape as POST run outputs. |

#### Response Status Codes

| HTTP | Code | Description |
| - | - | - |
| `200` | — | Status returned successfully. Check `status` field. |
| `401` | `INVALID_API_KEY` | `X-API-KEY` header is missing or invalid. |
| `404` | `REQUEST_NOT_FOUND` | No run request found for `requestId`, or it belongs to a different workspace. |
| `429` | `RATE_LIMIT_EXCEEDED` | Too many requests. Default limit is 5 req/sec per workspace. |
| `500` | `RUN_FAILED` | The run encountered an error. Message contains details. |

***

### 5.7 Rotate API Key

```text theme={null}
POST /api/v1/api-key/rotate
```

Generates a new workspace API key and immediately invalidates the current key used to make the request.

> **⚠ Important** — This action is irreversible. The moment this endpoint responds successfully, the key used to call it stops working. Update all integrations with the new key immediately.

#### Request

No request body is required. The workspace is identified from the `X-API-KEY` header.

```text theme={null}
POST /api/v1/api-key/rotate
X-API-KEY: sk-live-abc123xyz
```

#### Response — 200 OK

```json theme={null}
{
  "api_key": "sk-live-newkey9876xyz"
}
```

#### Response Fields

| Field | Description |
| - | - |
| `api_key` | The newly generated workspace API key. Replace your old key with this value immediately. |

#### Response Status Codes

| HTTP | Code | Description |
| - | - | - |
| `200` | — | New API key generated. Old key is now invalid. |
| `401` | `INVALID_API_KEY` | `X-API-KEY` header is missing or invalid. |
| `500` | `ROTATE_FAILED` | Key rotation failed. Retry or contact support. |

***

## 6. Sync vs Async Modes

When calling `POST /grids/:gridId/run`, you choose how to handle the response using the `mode` field.

### Sync Mode *(default)*

Set `mode: "sync"` or omit the field entirely.

* Server waits up to **120 seconds** for the run to complete.
* On success: outputs are returned inline in the response.
* On timeout: a `request_id` is returned; poll `GET /run/status/:requestId`.
* Best for: fast grids, interactive use cases, MCP tool integrations.

### Async Mode

Set `mode: "async"`.

* Server returns a `request_id` **immediately** (no waiting).
* Poll `GET /run/status/:requestId` every 2–5 seconds for results.
* Suitable for long-running grids (> 120 seconds).
* Best for: pipelines, batch jobs, background processing.

### Sync Flow

```text theme={null}
Client                              BitScale API
  │                                     │
  │── POST /grids/:gridId/run ──────────▶│
  │   { mode: 'sync', inputs: {...} }    │
  │                                     │── Runs row enrichments
  │                                     │   (waits up to 120s)
  │                                     │
  │◀── 200 OK { status: 'completed', ───│  (success within 120s)
  │            outputs: {...} }          │
  │                                     │
  │◀── 200 OK { status: 'running',  ────│  (timeout after 120s)
  │            request_id: '...',        │
  │            poll_url: '...' }         │
  │                                     │
  │── GET /run/status/:requestId ───────▶│  (poll every 2-5s)
  │◀── 200 OK { status: 'completed' } ──│
```

### Async Flow

```text theme={null}
Client                              BitScale API
  │                                     │
  │── POST /grids/:gridId/run ──────────▶│
  │   { mode: 'async', inputs: {...} }   │
  │                                     │
  │◀── 200 OK { status: 'running', ─────│  (immediate response)
  │            request_id: '...',        │
  │            poll_url: '...' }         │── Runs row enrichments
  │                                     │   in background
  │── GET /run/status/:requestId ───────▶│  (poll every 2-5s)
  │◀── 200 OK { status: 'running' } ────│
  │                                     │
  │── GET /run/status/:requestId ───────▶│
  │◀── 200 OK { status: 'completed', ───│
  │            outputs: {...} }          │
```

***

## 7. Error Code Reference

| Code | HTTP | Description |
| - | - | - |
| `INVALID_API_KEY` | `401` | The `X-API-KEY` header is missing, empty, or does not match any workspace. |
| `MISSING_REQUIRED_FIELD` | `400` | A required field is absent — most commonly `inputs` was not provided or JSON is malformed. |
| `INVALID_REQUEST` | `400` | A field value is invalid — e.g. `mode` is neither `"sync"` nor `"async"`. |
| `MISSING_SOURCE` | `400` | The grid has no BitScale API source configured. Add one in grid settings. |
| `INVALID_SOURCE` | `400` | The `source_id` provided does not match any BitScale API source on this grid. |
| `MISSING_SOURCE_COLUMN` | `400` | The grid has no source (DataSource) column — unexpected grid configuration error. |
| `INVALID_INPUTS` | `400` | The `inputs` object could not be serialized. Ensure all values are JSON-serializable. |
| `INVALID_OUTPUT_COLUMN` | `400` | One of the `output_columns` keys specified in `GET /grids/:gridId/curl` does not exist in the grid. |
| `GRID_NOT_FOUND` | `404` | No grid exists for the given `gridId`, or it belongs to a different workspace. |
| `WORKSPACE_NOT_FOUND` | `404` | No workspace found for the provided API key. |
| `REQUEST_NOT_FOUND` | `404` | No run request found for the given `requestId`, or it belongs to a different workspace. |
| `FEATURE_DISABLED` | `403` | BitScale APIs is an Enterprise-only feature. Upgrade your workspace plan. |
| `RATE_LIMIT_EXCEEDED` | `429` | Too many requests. Default is 5 req/sec per workspace. Contact [team@bitscale.ai](mailto:team@bitscale.ai) or your account manager to adjust. |
| `RUN_FAILED` | `500` | The row run failed during execution. Check the error message for details. |
| `ROTATE_FAILED` | `500` | API key rotation failed unexpectedly. Retry or contact BitScale support. |
| `INTERNAL_ERROR` | `500` | An unexpected server error occurred. Contact support if this persists. |

***

*© BitScale · api.bitscale.ai*


This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.