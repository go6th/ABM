# Speed audit, 2026-10-27

Why a research took far longer than the same question asked on Claude.ai, and why opening an
account was slow. Numbers are from staging logs (`gcloud logging read`, 14 days for model calls, 5
days for requests) and from the run history in the local database; every figure below is measured
unless it says "estimate".

## 1. Research

### Where the time went

| # | Cause | Evidence | Fix |
| --- | --- | --- | --- |
| 1 | Nearly every model call went through the Message Batches API (half price, answers in minutes) and was polled every 45 s | 634 batches in 14 days: median 3 min, p95 5 to 9 min, longest 34 min. The judge's ~130 token verdicts waited a median 137 s each (290 of them). 992 batches submitted against 644 finished: 72 waits were cut by a slice ending and re-attached | `batch.mode: fast` in `config/models.yaml` sends standard requests (seconds). Short-answer kinds (`source_reread`, `narrative_reread`, `abm_sense_check`, `second_pass_posting`, `classify_posting`, `classify_person`) are never batched, in either mode. `RESEARCH_SPEED=economy` brings the half-price batches back with no code change |
| 2 | Phase 1 pull was one provider call at a time: 33 planned rows, three or more searches per row, each search about 3 s (p50 2.8 s, read_page p95 17 s) | `agents/graph.py` `pull` looped rows, `PullAgent.pull_row` looped queries | Rows pulled 8 at a time (`crosscut/fanout.py`), manifest rebuilt in plan order. Measured on the test harness with 0.1 s per call: 9.6 s one at a time, 1.4 s fanned out (7x); a spend pause still stops new rows and the rows in flight finish |
| 3 | Person-name searches and the follow-up pulls were sequential | `person_searches`, `targeted_pulls` | Same fan-out, results stored in query order |
| 4 | Job boards: every lane and keyword searched in turn, then every posting read (page read about 2 s, up to 36 s) and classified one at a time | `jobs/pipeline.py` `_board`, `run_pass` | Board searches and postings run 8 at a time; the page-read and extractor budgets are taken before a call starts, so the caps hold |
| 5 | The three reading steps (pass 1, pass 2, people) ran one after another, each waiting for its slowest call | `_extract_steps` | One gate of 32 shared by the three steps (their rows are disjoint) |
| 6 | The grading judge was asked one claim at a time inside the grading loop | `grading/pipeline.py` | Claims are built, judged 12 at a time, then applied in the original order |
| 7 | A 429 on a standard call failed the node, which then replayed its siblings | `_send` had no wait | A 429 waits the provider's `retry-after` (else 2 s doubling to 60 s), at most 6 requests |
| 8 | Every provider call holds a slot from the governor (`app/container.py` `with_governance`), and every provider's ceiling was 1 "until measured", so no code could overlap two calls to one provider | `config/parallel.yaml` `providers` | Floor 4, default ceiling 4, `p_search` 8, run share 100 %. The connectors' token buckets still pace each provider to its documented rate (search 10/s, people 5/s, org 10/s; scrape and signals 1/s are placeholders marked `[VERIFY]` in `providers.yaml`: once the Firecrawl plan is known, raise `p_scrape` there and the page reads speed up with it) |

Not changed: the stage order itself. `config/workflow_v1.yaml` still has `execution_mode:
sequential`; the parallel scheduler exists (`workflow/parallel_phase.py`) but its provider
ceilings are 1 "until measured", and the live graph is the ABM graph in `agents/graph.py`, which
does not use it.

### What it costs

Fast mode drops the batch discount, so the model cost of the calls that were batched roughly
doubles. In the 14-day staging log sample the batched calls cost $4.26 at half price; at full
price that is about $8.5. Provider (search, scrape, people) cost does not change. Turn it back
with `RESEARCH_SPEED=economy`.

### What was not measured

No paid research was run for this audit. The per-stage speedups for pulls are measured on the
test harness with a stubbed provider; the end-to-end minutes on a real account are an estimate
until one run is made on the new code.

## 2. Website and API

Staging, 5 days of request logs: `GET /v1/accounts/{domain}/workspace` p50 1.5 s, p95 11.8 s (the
page `/accounts/{domain}` p50 1.3 s, p95 10 s); every trivial authenticated read 60 to 140 ms;
`/v1/runs/{id}` and `/progress` polled about 2,250 times each.

| Cause | Fix |
| --- | --- |
| Every read opened its own audit transaction (a tenant-wide lock, re-writing a row already written inside the 60 s window) | A read already recorded within its window skips it (`api/guard.py`) |
| A connection ping on every checkout, several checkouts a request | Ping only after 20 s idle or when last used on another event loop (`db/session.py`) |
| Session lookup in two transactions | One (`api/auth.py`) |
| Workspace route: 4+ transactions, ~30 queries to find the tables the files print, every account loaded to find one, workbooks parsed on a cache miss from a 6-book cache | One transaction, batched queries, one account, row counts cached by file content, workbooks opened concurrently, 16-book cache; logs `workspace.timing` |
| The run page re-fetched `/progress` on every poll tick as well as over the event stream | Not re-read on the tick; the run record polls every 5 s (was 3 s) |

Measured on a local API with a simulated 3 ms database round trip (median of 15): workspace (large
account) 357 to 166 ms, workspace (small) 312 to 120 ms, `/v1/me` 201 to 102 ms, `/v1/accounts`
170 to 71 ms, `/progress` 150 to 52 ms. Staging was not measured before the deploy; the 8 to 12 s
workspace case is a cold instance parsing a workbook (hypothesis, not reproduced).

Recommended, not applied: API to 2 vCPU with CPU throttling off (the SSE streams share one
throttled vCPU with every request). Not done: `/v1/me` still runs ~12 sequential queries,
`workspace()` ~24, and each open stream polls the database every second.
