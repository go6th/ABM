# 04 Run lifecycle, durability and the research graph (ARCH ch. 5, 6)

## Run state machine
`QUEUED → RUNNING → DISTILLING → VERIFYING → COMPLETED`; side states `AWAITING_APPROVAL` (human gate), `PARTIAL` (some facts Blocked), `BUDGET_EXCEEDED`, `FAILED`, `CANCELED`. Terminal: COMPLETED, PARTIAL, FAILED, CANCELED, BUDGET_EXCEEDED. **Each terminal state settles the wallet reservation** (commit actual, refund remainder) and releases the checkpoint for GC after retention. Transitions are single `UPDATE ... WHERE status = <expected>` (optimistic) so duplicate deliveries are harmless.

## End-to-end sequence
1. `POST /v1/runs` with Idempotency-Key. 2. One transaction: set tenant context, reserve credits, insert run (+ outbox). 3. Send `research/run.requested` with **event id = run id**. 4. Return `202 {run_id, status: QUEUED}`. 5. Inngest step `execute_slice(run_id, budget=240s)`. 6. Worker opens its own dedicated connection, sets tenant, loads checkpoint. 7. Tool and LLM calls via the ledger and cached prefix. 8. Checkpoint per superstep; facts and ledger rows written. 9. Return `{more: true}` or `{done: true}`. 10. Repeat until done (max 60 slices = 4 h). 11. Settle wallet; status terminal. 12. `run.completed` triggers alert evaluation, webhooks, notify.

## One owner per failure class (never retry in two layers)
| Failure | Owner | Behaviour |
| --- | --- | --- |
| Broker 429/5xx, LLM overload | LangGraph node `RetryPolicy` | 3 attempts, exponential backoff with jitter, then fact **Blocked**; run continues |
| Pod eviction, OOM, node loss, deploy | Inngest step retry + checkpoint | Next slice resumes from last checkpoint; ledger prevents re-billing |
| Slice time budget reached | Runner | Return `more=true`; Inngest schedules the next slice |
| Poison input, schema failure | Node, then run | **No retry**; fact Blocked with reason; run may finish PARTIAL |
| Budget exceeded | In-graph budget guard | Stop fan-out, distil what exists, `BUDGET_EXCEEDED` |
| Inngest outage or lost event | Reconciler | Re-send events for runs QUEUED > 60 s (idempotent) |
| Regional failure | DR runbook | Resume from replicated checkpoints in standby |

## Time-sliced execution
Do not hold one multi-hour step. The Inngest function loops; each iteration is a short step calling the worker with a time budget (default **240 s**). The worker resumes the graph from its checkpoint, runs until the budget expires or the graph ends, returns. Because LangGraph checkpoints every superstep, cancelling at the deadline loses at most the node in flight, and the ledger makes that node's paid calls free to replay. HITL: a graph interrupt returns `awaiting=true`; the function waits for an approval event (24 h timeout) then resumes. Cancel: endpoint sets CANCELED; runner checks status between supersteps. Fairness: per-tenant concurrency key.

## Tool-call ledger: exactly-once billing
Every paid call (broker or LLM) goes through `ledgered_call()`. Key = sha256 of tool name + canonicalised args (`sort_keys=True, separators=(",", ":")`), unique per run (`UNIQUE (run_id, args_hash)`). A replay after a crash returns the stored response without calling the vendor. Large responses go to S3, referenced by key. **The guarantee is exactly-once billing, not exactly-once side effects**: a crash between the vendor call and the ledger insert can repeat one call; keep calls idempotent where the vendor supports idempotency keys; the residual risk is bounded to one call per crash.

## Budgets and circuit breakers (enforced **inside the graph, before the call**)
Credits reserved per run (API, before dispatch) · broker spend cap per run (default $5.00 hard) · LLM token cap per run per depth tier · tool calls per run 60 · planner loops 2 · wall clock 4 h (slice counter) · per-tenant daily spend cap · per-broker concurrency via Redis token bucket. The orchestrator is only a backstop; it sees spend after the fact.

## Version safety
Graph version recorded on the run at creation; a run always resumes on its own version; state carries `schema_version` and a migration function upgrades old checkpoints on load; prompts are versioned and packaged with the graph.

## The research graph
`START → baseline → plan → budget_guard → fan_out (Send) → [news+events | jobs+hiring | people+leadership | tech+firmographics | private RAG (Phase B)] → quarantine_extract → merge+dedupe → distill_grade → verify → render_public / render_internal → END (emit run.completed)`. Gap-finding may loop back to `plan` **at most 2 times**.

| Node | Tier | Tools | Output | Idempotent |
| --- | --- | --- | --- | --- |
| baseline | Extractor | domain resolver, one cheap overview call | entity card, initial facts | yes (ledger) |
| plan | Planner | none | typed list of research branches and gaps | yes |
| budget_guard | none (code) | none | allow or stop fan-out | yes |
| fetch_branch (xN) | none (code) | one broker adapter each | raw payload reference | yes (ledger) |
| quarantine_extract | Extractor | **none** | schema-valid JSON facts with quote spans | yes |
| merge | none (code) | none | deduplicated facts with lineage groups | yes |
| distill_grade | Planner | none | graded facts, conflicts flagged | yes |
| verify | Judge | none | citation check, contradiction check, gap list | yes |
| render_public / render_internal | Planner | none | deliverable documents | yes |

Mapping to the PRD's 13 steps: set up and Pass 1 → `baseline` + a relationship step; baseline, news, jobs, tech, people → `fetch_branch` branches; person-name searches wait for the people branch (a dependent second fan-out); verification → `verify` (+ the three-query rule for Leads); distill → `distill_grade`; build and gate → render nodes + gate service; deliver → run completion + delivery note.

## Model tiers (config, never hard-coded IDs)
Planner (plan, distill_grade, render): strong reasoning, moderate cost. Extractor (baseline, quarantine_extract, normalisation helpers): fast, cheap, reliable structured output. Judge (verify, offline eval): highest accuracy and **must differ from the generator**, used sparingly. Embedding: 1536 dims via the model's `dimensions` option. Rerank: < 300 ms for 50 candidates. Illustrative defaults in ARCH (**[VERIFY]**): Sonnet-class `claude-sonnet-5-5`, Haiku-class `claude-haiku-4-5-20251001`, Opus-class `claude-opus-5-5`, `text-embedding-3-large` at 1536. Fallback chain within a tier before degrading quality; a degraded run is marked. Model IDs change only through the evaluation gate and use dated identifiers where offered. SAAS advice: start with the stronger model on distill and build; test a faster model on pulls; measure gate-failure rate per step and model before committing.

## Prompt-caching playbook
Prefix order: tool definitions → system prompt and SOPs → stable reference text → run-specific data → conversation turns. **Cached prefix contains no tenant data** (reusable across tenants; tenant and account data after the last breakpoint). Deterministic bytes: tools sorted by name, canonical JSON, no timestamps, request ids or random ordering in the prefix. Explicit `cache_control` breakpoints at the end of the static prefix and, in long loops, at the end of accumulated history. Prefixes below the model's minimum cacheable length are not cached (pad the SOP block rather than splitting it). Default lifetime is minutes: slices pausing longer lose the cache, so use the longer-lifetime option for the static prefix and warm it at slice start. Telemetry: record cache-read, cache-write and uncached input tokens on every call; **alert when cache-read ratio < 70%** on cacheable calls. All limits and multipliers **[VERIFY]**.

## Fact normalisation (code, not model)
Facts are `entity + attribute_key + normalised value` (e.g. `exec.cto.name`) with `fingerprint = hash(entity, attribute, normalised value)`. Normalise case, titles, currency, dates and domain forms in code so equal facts always collide and deltas are exact. Facts are append-only with `superseded_at`.
