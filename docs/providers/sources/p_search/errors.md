> ## Documentation Index
> Fetch the complete documentation index at: https://docs.parallel.ai/llms.txt
> Use this file to discover all available pages before exploring further.

# API Error Codes and Warnings

> Breakdown of warnings and errors

<div className="sr-only" aria-hidden="false">
  For AI agents: a documentation index is available at [https://docs.parallel.ai/llms.txt](https://docs.parallel.ai/llms.txt). The full text of all docs is at [https://docs.parallel.ai/llms-full.txt](https://docs.parallel.ai/llms-full.txt). You may also fetch any page as Markdown by appending `.md` to its URL or sending `Accept: text/markdown`.

  For Search and Extract, use `/v1/search` and `/v1/extract` for all new integrations and agent tool calls. Use `/v1beta/search` and `/v1beta/extract` only when maintaining an existing integration that already uses them. Do not substitute `/v1` for the documented FindAll or Ingest endpoint paths.
</div>

The Task API may return various warnings and errors during operation. This page documents the possible error types you might encounter when using the API.

## Errors

Errors result in a failure to process your request and are returned with appropriate HTTP status codes (4xx or 5xx).

| Error | Description | Resolution |
| - | - | - |
| **Invalid JSON Schema** | The JSON schema provided in the task spec for input or output is invalid. | Review your schema against JSON Schema specifications and ensure it follows the required format. |
| **Task Spec + Input Too Long** | The combined task specification and input exceeds 25,000 characters. | Reduce the size of your task spec or input data. Consider splitting into multiple tasks if necessary. |
| **Too-Complex Output Schema** | The output schema exceeds allowed complexity in terms of nesting depth or number of fields. | Simplify your output schema by reducing nested levels to 3 or less. |

## Warnings

Warnings indicate potential issues that don't prevent the request from being processed but may affect results.

| Warning | Description | Resolution |
| - | - | - |
| **Input Fails Validation** | The provided input does not conform to the input schema. | Verify your input against the schema requirements and make necessary adjustments. |
| **Task Spec + Input Over Size Limit** | The combined task specification and input exceeds the character limit. | Consider optimizing your input or task spec for better performance. |
| **Too Many Output Fields** | The number of requested output fields exceeds the recommended limit. | Consider reducing the number of output fields. |

## Warning Handling

The Task API uses a warning system to provide guidance without blocking execution. Warnings are generated during validation and can be handled in two ways:

### Basis Properties

It is recommended to use `FieldBasis` in the run output rather than requesting similar information in the output schema.

This means you've included fields like `citations`, `reasoning`, or `sources` in your output schema, but this information is **already provided automatically** in every Task Run result through the [Basis](/task-api/guides/access-research-basis) feature.

**What is Basis?** Every Task Run result includes a `basis` array containing citations, reasoning, and confidence levels for each output field. This is provided automatically; you don't need to request it in your schema.

**Why not include these in the output schema?**

* **Redundant**: You'll get duplicate data, wasting tokens and processing
* **Less structured**: The automatic Basis provides properly structured citations with URLs and excerpts
* **Less reliable**: Asking the model to generate its own citations may produce less accurate results than the built-in citation tracking

The following field names trigger this warning:

* `citations`
* `confidence`
* `evidence`
* `reasoning`
* `source`
* `sources`
* `source_urls`

**Instead of this:**

```json theme={"system"}
{
  "properties": {
    "company_name": { "type": "string" },
    "reasoning": { "type": "string" },
    "sources": { "type": "array", "items": { "type": "string" } }
  }
}
```

**Do this:**

```json theme={"system"}
{
  "properties": {
    "company_name": { "type": "string" }
  }
}
```

Then access `output.basis` in the response to get citations, reasoning, and confidence for each field. See [Accessing Research Basis](/task-api/guides/access-research-basis) for details.

## Error Reference

| Status Code | Meaning | Retry? | Description | Resolution Approach |
| - | - | - | - | - |
| **401** | Unauthorized | No | Invalid or missing credentials | Verify API key and authentication headers |
| **402** | Payment Required | No | Insufficient credit in account | See [402 Troubleshooting](#402-payment-required-troubleshooting) below |
| **403** | Forbidden | No | Invalid processor in request | Check processor availability and permissions |
| **404** | Not Found | No | Run ID or resource not found | Verify run ID and resource existence |
| **408** | Request Timeout | Yes | Synchronous request timed out | Use asynchronous polling |
| **422** | Unprocessable Content | No | Request validation failed | Review error details and validate schema |
| **429** | Too Many Requests | Yes | Rate limited or quota exceeded | See [429 Troubleshooting](#429-too-many-requests-troubleshooting) below |
| **500** | Internal Server Error | Yes | Server-side processing error | Retry with backoff, contact support if persistent |
| **502** | Bad Gateway | Yes | Upstream service error | Retry, usually temporary |
| **503** | Service Unavailable | Yes | Service temporarily unavailable | Retry with backoff |

## Error Response Format

All errors return a consistent JSON structure:

```json theme={"system"}
{
  "error": {
    "message": "Human-readable error description",
    "detail": {
      // Additional error-specific information
    }
  }
}
```

For validation errors (422), the `detail` field contains specific information about which fields failed validation and why.

## 402 Payment Required Troubleshooting

A 402 error indicates your account has insufficient credits to process the request. This can occur even if your account shows a positive balance due to **in-flight balance reservations**.

### Understanding In-Flight Balance

When you submit a task, the system reserves credits for the estimated cost before processing begins. This reservation is called the "in-flight balance." Your **available balance** equals your **total balance** minus the **in-flight balance** from all currently running tasks.

**Example scenario:**

* Account balance: \$10.00
* 5 running `pro` tasks (each reserves \~$0.10): $0.50 reserved
* Available balance: \$9.50

If your available balance is insufficient for a new task, you'll receive a 402 error even though your total balance appears positive.

### Common Causes

| Cause | Solution |
| - | - |
| **Multiple concurrent tasks** | Running many tasks simultaneously reserves credits for each. Wait for some to complete or reduce concurrency. |
| **Higher-tier processors** | `ultra` and `pro` processors reserve more credits than `base` or `lite`. Consider using lower-tier processors for less complex tasks. |
| **Insufficient account balance** | Add credits at [platform.parallel.ai](https://platform.parallel.ai). |
| **Large task groups** | Task Group submissions check available balance for the full batch upfront; individual runs reserve credits when they begin executing. Split into smaller batches if needed. |

### How to Resolve

1. **Check your balance**: View your current balance and usage at [platform.parallel.ai](https://platform.parallel.ai)
2. **Wait for tasks to complete**: In-flight reservations are released when tasks finish
3. **Add credits**: Top up your account balance if genuinely low
4. **Reduce concurrency**: Lower the number of parallel tasks to reduce reserved credits
5. **Use lower-tier processors**: Reserve fewer credits per task by using `base` or `lite` processors where appropriate

<Tip>
  For high-volume workloads, monitor your available balance (not just total balance) and implement backoff logic when approaching limits. See [Pricing](https://parallel.ai/pricing) for processor costs.
</Tip>

## 429 Too Many Requests troubleshooting

A 429 error means you exceeded the [rate limit](/getting-started/rate-limits) for an endpoint. You can receive a 429 even when your logs show fewer requests than your limit in any single clock minute.

### Rolling-window rate limiting

Parallel enforces per-minute rate limits over a rolling one-minute window, not per calendar minute. Each new request counts against all requests you sent in the past 60 seconds, regardless of where clock-minute boundaries fall.

**Example scenario:**

* Rate limit: 600 requests per minute
* 300 requests sent at 12:00:49
* 300 requests sent at 12:01:00

The requests fall in different clock minutes, but all 600 were sent within the same 60-second window. Further requests are throttled until the requests from 12:00:49 are more than one minute old.

### How to resolve

1. **Wait for the window to roll over**: Once the rate limit is hit, requests are throttled until earlier requests become more than one minute old
2. **Retry with exponential backoff**: Space out retries instead of resending immediately
3. **Smooth out bursts**: Spread requests evenly over time rather than sending large batches at once
4. **Request higher limits**: Contact **[support@parallel.ai](mailto:support@parallel.ai)** if your workload needs more capacity
