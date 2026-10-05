# Prompt: build the broker connectors (production grade, with anti-hallucination rules, test harness and bounded loops)

Paste everything below the line into Claude Code, opened in the project repo (built with the `account-intelligence-platform` skill).

---

Build the **broker connector layer**: one typed connector for each of the five providers, with retry and backoff, response caching, a pull-once guarantee, routing rules from the tools playbook, and per-call cost recorded. This is skill work package 1.4. Write production-grade code, prove it with a real test harness, and never invent anything about a provider.

## 0. Ground rules

1. Read `.claude/skills/account-intelligence-platform/SKILL.md` first and obey it. Then read `references/04-run-lifecycle-and-agents.md`, `references/03-tenancy-isolation-security.md`, `references/05-data-model.md`, `references/13-starter-code-and-known-defects.md`, `assets/routing.yaml`, `assets/client_output_denylist.txt`, `BUILD_LOG.md`, `docs/STATUS.md` (if present). Follow the skill's work loop: plan in five lines, tests first, smallest increment, run the gates, log.
2. **The five providers are internal.** Use provider codes everywhere in code, storage keys, logs and metrics: `p_search`, `p_scrape`, `p_signals`, `p_people`, `p_org` (mapping is in `assets/routing.yaml`). Real vendor names appear only in one internal registry module and in internal docs. They never appear in object keys, file names, client-safe views, exports, error messages returned to clients, or metric labels.
3. **Precedence.** The skill (Architecture v2) wins over the roadmap. Retry ownership is the one place this matters, see section 3.4.
4. No real network calls to providers, no real spend, no real credentials, no real tenant data while building. Use fakes and recorded fixtures. A `live` smoke test may exist but is skipped by default and needs an explicit environment variable and a spend cap.
5. Never claim a gate passed that you did not run. Never weaken or delete a test to make code pass. Never mark a connector "done" if its contract is unverified (see section 1).
6. Commit on branch `feat/broker-connectors` in small Conventional Commits. Do not push.

## 1. Anti-hallucination protocol (build time)

You do not know these providers' real APIs from memory. Treat any recollection of endpoints, parameters, response fields, rate limits, credit costs or error codes as **unverified**.

1. **Provider spec first.** For each provider create `docs/providers/<code>.md` containing: base URL, auth scheme, each operation we use (method, path, parameters, pagination style), response shape, error codes, rate limits, cost model, with a **source citation (documentation URL or a file I supplied) and the date retrieved** for every line. Sources allowed: official documentation you can fetch, OpenAPI files or sample responses I put in `docs/providers/samples/`, or recorded responses from the PRD's tools playbook. Nothing else.
2. If official documentation is not reachable and I supplied no samples, **STOP for that provider** and tell me exactly what to supply (API docs link, an OpenAPI file, or two or three real sample responses per operation). Build the shared framework, the fake provider server and the other providers meanwhile. Do not guess.
3. Every fact in the spec that you could not source is tagged `[VERIFY]`, and the code that depends on it carries a `# VERIFY(<code>): <what>` comment and a skipped test named `test_verify_<what>`.
4. **Claim ledger.** At the end, write `docs/providers/CLAIMS.md`: a table of every provider-specific fact used in code (endpoint, field name, cost number, limit) with its citation. A claim without a citation is a defect; remove it or tag it `[VERIFY]`.
5. **Costs and limits are data, not code.** `config/providers.yaml` holds per-operation cost and rate limits, each with a `source` field. Unknown cost is `null` with `cost_source: unknown` and is surfaced in the call record and in a warning metric. Never default an unknown cost to 0. The PRD playbook hints (1 credit per plain scrape; JSON extraction about 5; search with scrape 20 or more; people by technology 1 credit per person plus per attribute; no visible cost for people by title) may seed the file but must be marked `source: PRD tools playbook, verify against current plan`.

## 2. Anti-hallucination protocol (run time)

These rules make the connector layer incapable of inventing evidence.

1. **Connectors never call an LLM and never summarise.** They return the raw payload reference plus deterministic, schema-validated normalised records. All normalisation is plain code.
2. **Strict response validation.** Each operation has a pydantic model for the fields we rely on. A response that fails validation is not repaired and not partially trusted: raise `SchemaDriftError`, store the raw payload for debugging, and let the caller mark the affected coverage row **Blocked with a reason**. Unknown extra fields are kept in the raw payload only.
3. **Missing is `None`, never invented.** No default of `""`, `0`, `[]` or "Unknown" for absent provider fields. Counts always carry their base (`read`, `matched`, `open`, `closed`).
4. **Provenance on every record:** `provider_code`, `operation`, `source_url` (as returned by the provider), `retrieved_at` (UTC), `raw_ref` (object key), `page` and `index`. A record without provenance cannot be constructed (enforce in the model).
5. **Lineage hint:** each record carries `lineage_hint` (the upstream origin if the provider states one, else `None`), so the grading engine can group sources. Do not guess lineage (open decision D-11).
6. **Untrusted text stays untrusted.** Page text, job descriptions and profile text are stored raw and passed on as data; connectors never interpolate them into prompts, queries, URLs or file names.
7. **Typed arguments only.** Argument models use `extra="forbid"`, have no `tenant_id` field, validate `domain` with the project pattern, and validate URLs: `https` only, host must resolve to a public address, no userinfo, no private, loopback or link-local ranges, the metadata address refused, redirects capped. For page-reading operations the host must be in the account's allow-list (account domain, its verified careers host, hosts from earlier validated sources). This is the SSRF defence; test it.
8. **Results must be reproducible:** the same stored raw payload always normalises to the same records (golden-file test).

## 3. Architecture to build

Package `src/connectors/`:

```
src/connectors/
  __init__.py        public API only: call_operation(), ConnectorResult, errors
  base.py            Connector protocol, Operation enum, CallContext, ConnectorResult
  models.py          pydantic: typed args per operation; normalised records with mandatory provenance
  errors.py          typed error hierarchy (see 3.4)
  http.py            httpx.AsyncClient factory: timeouts, limits, proxy from env, TLS verify on, size cap
  ratelimit.py       per-provider token bucket with shared cooldown (injectable store: in-memory, Redis)
  breaker.py         per-provider circuit breaker (closed, open, half-open), injectable store and clock
  cost.py            cost model from config/providers.yaml, returns (amount, unit, cost_source)
  ledger.py          ledgered call v2: per-run lease + intent row + pull-once (see 3.2)
  budget.py          atomic budget gate (see 3.3)
  paginate.py        bounded pagination engine (see 3.5)
  router.py          routes and fallbacks from config (see 3.6)
  security.py        URL validation, SSRF checks, secret redaction
  registry_internal.py  provider code to vendor name; imported by internal tooling only
  providers/
    p_search.py  p_scrape.py  p_signals.py  p_people.py  p_org.py
```

Every external dependency (HTTP transport, clock, sleep, random, store, payload store, DB session) is **injected**, so tests control time and failure. No module-level mutable state. All code passes `mypy --strict` for this package and `ruff`. No bare `except`, no `print`, no secrets in logs, aware UTC datetimes only, structured JSON logs with the field allow-list from the skill.

### 3.1 Contract

```python
class Connector(Protocol):
    code: ProviderCode
    async def execute(self, op: Operation, args: OperationArgs, ctx: CallContext) -> RawResponse: ...
    def normalise(self, op: Operation, raw: RawResponse) -> list[Record]: ...   # pure, deterministic
```
`call_operation(op, args, ctx)` is the only entry the graph uses. It runs: validate args, route, breaker check, budget reserve, ledger lease and intent, rate-limit acquire, execute, size and type checks, store raw payload (neutral key), validate schema, normalise, record cost, mark ledger done, return `ConnectorResult(records, raw_ref, pages, cost, provenance, status)`. `CallContext` carries `tenant_id`, `run_id`, `account_id` taken from the graph state, never from arguments.

Operations to support, mapped from the routing rules in `assets/routing.yaml`: open web search and known-page read (`p_search`); job boards and pages (`p_scrape`); news, technology and job feeds (`p_signals`); people by title and profiles, posts and comments (`p_people`); people by technology, organizations, tech stack and teams (`p_org`). Implement an operation only if its provider spec section is sourced (section 1). Otherwise leave it raising `NotSupportedYet` with a clear message.

### 3.2 Pull-once and the ledger (fixes known defect 3)

Key: `args_hash = sha256(canonical_json([provider_code, operation, args]))` with sorted keys and fixed separators; unique per `(run_id, args_hash)` (existing `tool_calls` table, extend it with an expand-only migration).
1. **Per-run lease:** only one worker may execute a run's paid calls at a time (lease row with owner id, expiry and heartbeat, or an advisory lock on the run's dedicated connection; choose, justify in an ADR). A second worker on the same run waits or yields.
2. **Intent row:** insert `(run_id, args_hash, status='started', attempt, started_at)` before the paid call; finalise to `done` (with response reference, cost, latency, http status) or `failed` (with error class) after. A stale `started` row left by a crash is reconciled on resume: if the provider supports idempotency keys, reuse the key; otherwise re-issue and set `possible_duplicate = true` so spend is reconciled against invoices.
3. **Replay:** a `done` row returns the stored response without calling the provider. Large payloads live in object storage, the row holds the reference.
4. **Feeds are pulled once per run.** A paged feed is one logical pull; each page is its own ledger key (cursor in the args) and the whole pull is replayable. A second request for the same feed in the same run returns stored pages. Refreshes use delta pulls ("found from last pull"), which are new keys.
5. The existing `xfail` test `test_concurrent_identical_calls_pay_once` must now pass; remove its `xfail`. Add: kill the process between provider response and ledger write, resume, assert at most one repeat and `possible_duplicate` set; two workers on one run assert no duplicate paid call.

### 3.3 Budget, cost, and call caps (inside the graph path, before the call)

`budget.py` reserves the estimated cost atomically: `UPDATE account_runs SET spent_usd = spent_usd + :est, tool_calls_used = tool_calls_used + 1 WHERE id = :run AND spent_usd + :est <= budget_usd AND tool_calls_used < tool_call_cap RETURNING ...`. No row means `BudgetExceeded` (the run ends BUDGET_EXCEEDED with partial results). After the call, true up the difference between the estimate and the actual cost. Defaults from the skill: $5.00 broker cap per run, 60 tool calls, per-broker concurrency via the shared limiter. Keep **credits and dollars in separate columns** (known defect 4); never reuse one for the other. Every call record stores `provider_code`, `operation`, `route`, `credits`, `cost_usd`, `cost_source`, `http_status`, `latency_ms`, `attempt`, `bytes`, `possible_duplicate`. A test sums the ledger for a run and asserts it equals the run's `spent_usd`.

### 3.4 Errors, retry, backoff (one owner per failure class)

The skill and Architecture 5.3 say broker 429 and 5xx retries are owned by the **LangGraph node `RetryPolicy`** (3 attempts, exponential backoff with jitter, then the fact is Blocked and the run continues). Do **not** add a second retry loop inside the connector; that causes retry storms and duplicate spend. Implement it like this:
- The connector classifies every failure into the typed hierarchy: `RetryableProviderError` (429, 5xx, timeouts, connection reset, transient network), `RateLimited(retry_after)` (subclass), `NonRetryableProviderError` (400, 404 for a valid resource, validation), `AuthError` (401, 403; page the operator, never retry), `CreditsExhausted` (provider says balance empty), `SchemaDriftError`, `BudgetExceeded`, `CircuitOpen`, `SecurityError`.
- On `429` or a `Retry-After`, the connector sets the **provider's shared cooldown** in the rate limiter so every worker backs off together (cap the honoured delay at a configured maximum), then raises `RateLimited`. Pacing is the connector's job; the retry decision is the node's.
- Provide `retry_policy_for(provider)` that returns the LangGraph `RetryPolicy(retry_on=RetryableProviderError, max_attempts=3, initial_interval=..., backoff_factor=2.0, jitter=True)` for the graph builder to attach. Backoff values come from config.
- Fallback routing (section 3.6) triggers only for `CreditsExhausted`, `CircuitOpen` or exhausted retries on an outage, never for `SchemaDriftError` or `SecurityError`.
- Mapping to the product: a provider outage or an empty balance marks the affected coverage rows **Blocked with a reason**; it never fails the run.
- If you conclude a different owner is better (for example Retry-After handling in the node is too coarse), write an ADR with evidence and **ask me before changing it**.

### 3.5 Loops (all bounded, all testable)

Every loop has a hard cap, a deadline and an exit reason that is recorded.
- **Pagination:** `max_pages`, `max_items`, `max_bytes`, wall-clock deadline tied to the slice budget, and **cursor-repeat detection** (a provider that returns the same cursor or the same page twice ends the loop with `PaginationLoopDetected`, not an infinite loop). Dedupe records across pages by stable key. Resume from the last stored page after a crash instead of restarting. The result says why it stopped: `exhausted`, `max_pages`, `max_items`, `deadline`, `loop_detected`; a non-`exhausted` stop marks the coverage row **Partial** with the base counts, never Done.
- **Async provider jobs** (if a provider spec has crawl or batch jobs): poll with bounded interval, jitter and total timeout; cancel on timeout; no unbounded `while True`.
- **Rate-limiter wait:** bounded wait with a maximum; beyond it, raise `RateLimited` so the node policy owns the retry.
- **Cancellation safety:** `asyncio.CancelledError` at a slice deadline must leave the intent row in `started` (reconcilable) and must never leave a half-written payload; write payloads atomically (temp key then rename or a single put).

### 3.6 Router and the playbook rules

Load routes from config (derived from `assets/routing.yaml`: need, first provider, fallback, cost note). Enforce the adapter rules in code and in tests:
- Never route `p_org` output to a hosted file or client-visible store.
- Plain `p_scrape` fetches only on job pages; JSON-extraction and search-with-scrape modes are refused unless explicitly enabled per call with a cost reason.
- `p_people` results are paged and de-duplicated by profile URL; do not re-add sources already paged.
- `p_signals` is pulled once per run and saved.
- The routing `fallback: web_search_builtin` and `fetch_builtin` refer to the chat skill's built-in tools, which do not exist in the service. Treat those fallbacks as `p_search` operations or as unsupported, and record this as an open item for me. Do not invent a substitute.
- A route with no available provider returns `Blocked(reason)`.
- Every fallback taken is recorded on the call record (`route`, `fell_back_from`).

### 3.7 Resilience, security, observability

- `httpx.AsyncClient` per provider with explicit connect, read, write and pool timeouts and a total deadline; connection limits; response size cap enforced while streaming; content-type check; TLS verification always on; egress through the configured proxy; gzip handled.
- **Circuit breaker** per provider: opens after N consecutive retryable failures in a window, half-open probe after a cooldown, state in an injectable store. It is the backstop, not the retry mechanism.
- Secrets come from the environment or mounted files, are wrapped in a `SecretStr`-style type, never logged, and are redacted from exceptions and from recorded cassettes.
- OpenTelemetry span per call with attributes `provider_code`, `operation`, `route`, `attempt`, `cost_usd`, `cost_source`, `outcome`; run id is the trace id. Metrics aggregated by provider code, operation and outcome only (no tenant label).
- Expand-only Alembic migrations for new columns and tables; forced RLS and `TO app_rw` roles on any new tenant table; update `check_rls_catalog.sql` expectations if needed.

## 4. Test harness (build it before the providers)

Location `tests/connectors/`. Use `httpx.MockTransport` or a small local fake HTTP server, real Postgres (the project's test database), and injected clock, sleep and random so no test sleeps for real.

1. **Fake provider server** with scripted scenarios per call: success, empty result, 429 with and without `Retry-After`, 500, 503, timeout, connection reset, truncated body, malformed JSON, wrong content type, oversized body, schema drift (renamed or missing field), 401, 403, credits exhausted, duplicate page, repeating cursor, endless pagination, slow response around the slice deadline.
2. **Contract tests per provider** against recorded cassettes built from the sourced samples (secrets scrubbed, a test that fails if a cassette contains a key-like string). A cassette whose shape changes must fail loudly.
3. **Golden normalisation tests:** stored raw payload to expected records, byte for byte.
4. **Property tests (hypothesis):** `args_hash` is stable under key order and changes with any argument; backoff values never exceed their cap; token bucket never grants more than its rate over any window; pagination always terminates within its caps for any generated page sequence; no normalised record can be built without provenance.
5. **Concurrency tests:** 50 concurrent identical calls on one run give one provider call; two workers on one run give no duplicate paid call; parallel runs of different tenants never see each other's ledger rows (isolation suite extended).
6. **Kill and resume tests:** terminate the worker (a subprocess) between provider response and ledger finalisation, and mid-pagination; resume; assert page continuity, at most one repeat, `possible_duplicate` set when applicable, and the run's `spent_usd` equals the ledger sum.
7. **Budget tests:** the cap is never exceeded under concurrency; the 61st call is refused; estimate versus actual true-up; unknown cost is flagged, not zero.
8. **Policy tests:** adapter rules from 3.6; SSRF table (private ranges, metadata address, userinfo, non-https, redirect to private, DNS to private); no provider name appears in any object key, log line, error returned to a client, or client-safe view (scan with `assets/client_output_denylist.txt`).
9. **Failure-to-product tests:** a provider outage yields a Blocked coverage row with a reason and the run still completes as PARTIAL; schema drift yields Blocked, not wrong data.
10. **Live smoke** (marker `live`, skipped unless `LIVE_SMOKE=1` and a spend cap is set): one cheap call per provider, asserting only the contract.

Coverage target: at least 90% line coverage on `src/connectors/` with 100% on `ledger.py`, `budget.py`, `ratelimit.py`, `paginate.py`, `security.py`. Optional but encouraged: run `mutmut` on `ratelimit.py`, `budget.py` and `paginate.py` and report the surviving mutants.

## 5. The development loop (bounded, so you cannot spin or paper over failures)

For each component (framework pieces first, then each provider) repeat at most **5 iterations**:
1. Write or extend the failing tests from section 4.
2. Implement the smallest change.
3. Run the component's tests, then `ruff`, `mypy --strict src/connectors`, and the isolation suite.
4. If green, log and commit. If red, read the failure, change the **code**, and rerun.

After 5 red iterations on one component, STOP and report: the failing test, its output, what you tried, and your best hypothesis. Do not weaken the test, do not mock the unit under test, do not skip it, and do not mark it `xfail` unless it documents a defect that is already written in `references/13` or in an ADR.
Build order: `errors`, `models`, `security`, `http`, `ratelimit`, `breaker`, `cost`, `budget`, `ledger`, `paginate`, `router`, fake server and harness, then providers one at a time (`p_signals`, `p_search`, `p_scrape`, `p_people`, `p_org`) with each provider's spec file written first.

## 6. Gates before you finish

Run and report real results for each: the skill self-test; `validate_assets.py`; `lint_migrations.py`; `check_rls_catalog.sql` (no rows); `ruff check`; `mypy --strict src/connectors`; the full isolation suite; `pytest tests/connectors` with coverage; the whole `pytest -m "not eval"`. Then re-check section 1.4: `docs/providers/CLAIMS.md` has a citation for every provider fact, and every `[VERIFY]` is listed. A grep of `src/` and `tests/` for the vendor names must hit only `registry_internal.py`, `docs/providers/` and `config/providers.yaml` display fields.

## 7. Deliverables

`src/connectors/` as above; migrations; `config/providers.yaml`; `docs/providers/<code>.md` (five) and `docs/providers/CLAIMS.md`; ADRs for the lease design and the retry-owner decision; `tests/connectors/`; updated `BUILD_LOG.md` and `docs/STATUS.md`; the `xfail` removed from the ledger concurrency test.

## 8. Stop rules

Stop and ask me if: a provider's API cannot be sourced (section 1.2); a decision from D-01 to D-11 blocks you and has no default; a change would drop or alter an existing column; you want to change the retry owner; real credentials, real spend or real tenant data would be needed; a gate stays red after 5 iterations; or you find the skill and the roadmap in conflict outside what is listed here.

## 9. Final reply (short)

A table of provider by operation by status (Done, Partial, Blocked with the exact input I must supply); gates run with results; the ledger and budget guarantees as actually proven by tests (state precisely what is and is not guaranteed); open `[VERIFY]` items; the fallback decision for the chat-only built-in tools; commits; the next work package.
