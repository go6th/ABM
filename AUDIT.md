# Durability audit: where paid data can be lost when a run stops

Date: 2026-10-13. Branch `feat/durable-runs`. Read-only pass over the run path (API, worker
slice, LangGraph graph, connector ledger, model port, builders) before any code change. Each item
names the code path, what is already durable, and what is lost if the process dies right there.

## What is already durable (and relied on below)

* **Paid provider calls**: `connectors/ledger.py` writes a `started` intent row and a budget
  reservation in one transaction before the HTTP exchange; the raw body is stored under a
  content-addressed key (`rt.payloads.put`) before parsing; the `done` row (cost, payload
  reference) is written in `finalize_done`. A finished key replays within the run (pull once) and,
  since 2026-10-13, across runs of the same tenant (research cache).
* **Model replies (legacy port, the live path)**: `workflow/deps.py _legacy_model` stores the raw
  reply under the run and logs tokens and cost to `model_reply_log` before the caller parses it.
* **Graph state**: `platform/checkpoint.py` is the LangGraph Postgres saver; every node's output is
  checkpointed before the next node runs; a slice resumes from the last checkpoint.
* **Facts, manifests, distill output, deliverables**: written through single write paths to
  tenant tables as each node ends.

## Findings (paid or produced data at risk)

| # | Code path | What lives only in memory or only at the end | Lost if the process dies there | Severity |
| --- | --- | --- | --- | --- |
| A1 | `connectors/service.py _fetch_page_claimed`, between `exchange` returning and `rt.payloads.put` | The paid HTTP body | The body and its ledger link. On resume the `started` attempt is stale, closed `abandoned` and the call re-issued once, flagged `possible_duplicate` (paid twice). Unavoidable window of one network read; bounded to one repeat. | medium, bounded |
| A2 | same function, between `rt.payloads.put` and `finalize_done` (parse, post checks) | The ledger `done` row (cost, payload reference) | The payload is stored but orphaned; same re-issue as A1. Worse: a non-connector exception inside `connector.parse` (a parser defect) reaches `except Exception` and closes the attempt `failed` at zero cost although the body was paid for and stored, so the payload is never linked and the next attempt pays again. | high |
| A3 | `connectors/service.py _poll`: a provider job result is stored, then parsed, then returned to the caller which finalises | As A2 for asynchronous provider jobs | As A2. | high |
| A4 | `llm/port.py Router.call` (router path, off by default): a reply that fails its schema twice is recorded as an attempt only | Tokens, cost and the raw text of the two paid replies | Spend unrecorded; nothing replays. The legacy port does not have this gap. | medium (path inactive) |
| A5 | `workflow/deps.py _legacy_model`: an empty reply is not stored for replay | Nothing paid is lost (the log row carries its tokens); the re-run asks again | Acceptable: an empty reply has no value. | low |
| A6 | `agents/graph.py pull`: the manifest is built in memory over 33 rows times 3 phrasings and saved once at the end of the node | The manifest | Nothing paid: every call and payload is in the ledger and the store; the re-run of the node replays every call free and rebuilds the manifest. Time only. | low |
| A7 | `agents/graph.py _interpret`: extraction candidates of up to 8 concurrent model calls are held until `grading.grade` runs once at the end | Candidates and people | Nothing paid: replies replay from the run's stored replies at zero usage; grading reruns. | low |
| A8 | `workflow/runner.py _slice`: `except Exception` ends ANY unexpected error `FAILED` with a full refund, whatever the run has already stored | The run's standing | Pulls, facts and drafts stay in the tables but the run is reported FAILED, the client sees nothing, and the pulls are not offered for reuse. Observed on 2026-10-12: four runs with 50 USD of stored pulls ended FAILED on a model 400. | high |
| A9 | `workflow/runner.py _slice`: `asyncio.CancelledError`, `KeyboardInterrupt` and `SystemExit` (uvicorn SIGTERM or SIGINT shutdown, a cancelled task) are not handled; nothing records why the run stopped | The stop reason | The run stays RUNNING forever on the local stack (no Cloud Tasks redelivery, no sweeper). In the cloud, redelivery resumes it; locally it is stuck until someone looks. | high (local), medium (cloud) |
| A10 | Worker crash, OOM kill, deploy: no process survives to write anything; the run lease (`run_leases`, TTL 300 s) expires | The RUNNING status is never resolved | Same as A9: stuck RUNNING with its data in place; no component ever marks it. | high |
| A11 | `api/routes/runs.py GET /v1/runs/{id}`: returns status, `finished_reason` and the coverage view only | What a PARTIAL run actually holds | A client cannot see the stored pulls, facts, drafts, or an explicit list of what is missing and why. Exports refuse a run whose tables are not ready. | medium |
| A12 | Resume: nothing lets a new run declare that it continues an earlier one; the research cache reuses by TTL only | Reuse of a stopped run's finished work past the TTL, or when the cache is off | A re-run after the TTL pays again. | medium |
| A13 | `tracker/guard.py finish_run` stores `finished_reason` as a short code; error messages are dropped by the structured logger (`message_hash` only) | The error detail | An operator finds the cause only by reading the LangGraph checkpoint by hand (see `debug-failed-runs`). | medium |
| A14 | Secrets in stored text: provider error bodies are stored as `response` meta on failed paid attempts; the model SDK error message is what the runner would store as a detail | A provider or SDK error text could carry a key | Not observed, but no redaction exists on the stop-detail path that A13 adds. | medium (preventive) |

## Stop reasons against the findings

| Stop reason | Today | Gap |
| --- | --- | --- |
| provider out of credits | row Blocked `credits_exhausted`, run continues (D-31) | none |
| model provider out of credits | ABM: PARTIAL `model_credits_exhausted` (2026-10-13); Workflow v1: FAILED (A8) | A8 |
| missing or invalid key | `AuthFailed`/`NotConfigured`: ABM PARTIAL; Workflow v1 FAILED | A8 |
| model refusal | `ContentRefused`: ABM PARTIAL; Workflow v1 FAILED | A8 |
| truncated or invalid model reply | stored, logged, one re-ask, then the item is blocked or unclassified | none (legacy port); A4 (router) |
| prompt too long | ABM: group dropped; Workflow v1: FAILED | A8 |
| timeout, rate limit, network drop | `Overloaded`/`RetryableProviderError`: node retry, then Blocked row or FAILED | A8 for the model side after retries |
| unhandled exception | FAILED with full refund regardless of stored data | A8, A13 |
| worker crash, restart, OOM, SIGTERM, SIGINT | stuck RUNNING locally; redelivered in the cloud | A9, A10 |
| user cancel | CANCELED, settled, stops within seconds (2026-10-12) | none |
| failed final build | Workflow v1 PARTIAL `build_failed`; ABM PARTIAL `build_failed:<reason>` (2026-10-13) | none |
| any stop: what is stored | tables hold it; the API does not show it | A11, A12 |

## What the build changes (summary; details in BUILD_LOG.md)

A2, A3: a stored paid body is always finalised `done` (with `error_class` when parsing refused it)
before any exception leaves the call. A8: an unexpected error ends PARTIAL with `stop_reason` and
a redacted `stop_detail` when any finished paid call, fact or deliverable exists; FAILED only
otherwise. A9: cancellation and interrupts record `stop_detail` and leave the run resumable. A10:
an orphan sweeper (startup and periodic) marks RUNNING runs whose lease is long expired PARTIAL
`worker_lost`. A11: `GET /v1/runs/{id}` gains `stop_detail`, `stored` counts and a `missing`
list; `GET /v1/runs/{id}/collected` exposes facts, drafts, pulls and the missing list. A12:
`run_input {"resume_from": "<run id>"}` reuses every finished call and reply of that run at cost
0 regardless of TTL; a `started` row is never a hit; stale `started` rows of a resumed slice are
re-issued only after 15 minutes. A13, A14: `account_runs.stop_detail` with secret redaction. A4 is
documented, not changed (the router path is off and gated by a benchmark decision).
