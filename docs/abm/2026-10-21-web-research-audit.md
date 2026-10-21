# Web research audit, 2026-10-21

A read-only audit of the web research product, from "Research a new company" to the data on
screen. Nothing was run against a paid provider or model while it was written. Line numbers are
from the working tree on 2026-10-21 (which also holds another session's uncommitted run-controls
work) and will move.

The owner's three complaints, and what the code says about each:

1. **"It does not show what research is going on."** There is no page that lists research.
   `/runs` is a redirect stub (`web/app/(client)/runs/page.tsx`). `GET /v1/runs` exists and nothing
   in the web app calls it. A job that has not started its run yet (`research_jobs` with no
   `run_id`) is in no list at all, only on its account's page.
2. **"Data is stored but shows late."** Two causes stack:
   * the worker writes facts in one batch at the very end of the `interpret` node, after every
     extraction call and every judge call (`src/agents/graph.py:389-472`);
   * the browser hears about steps only, every 2 s, and only for the first 60 s: the server ends
     each event stream after `sse_max_duration_s: 60` and the client then drops to 5 s polling for
     good (`web/components/ws/RunSteps.tsx:96-103`). Counts, findings and sheets on the account
     page are not refreshed while a run is going at all.
3. **"The research does not bring the data."** Four stacked causes, detailed in section 3: money
   accounting stops a run after about 20 paid calls, the queries are placeholder text, page reads
   are limited to the company's own site, and paid-for results are thrown away after the pull.

## 1. How a run works today

| Stage | Where | What is written, and when |
| --- | --- | --- |
| Start | `POST /v1/research` (`api/routes/research.py`) | One transaction: account, limit, `research_jobs`, credit reservation, audit. Then the job is enqueued. |
| Claim | `/tasks/research-job` → `replica/jobs.py run_job` | `lifecycle.create_run`: `account_runs` + `runs`, 33 `coverage_rows` seeded, slice 0 enqueued. |
| Slices | `/tasks/run-slice` → `workflow/runner.py execute_slice` | Lease (`run_leases`, 1500 s), 1200 s slices, at most 8. A node cut by the slice timeout re-runs from its start. |
| `pull` | `agents/graph.py:281` | Sequential over 33 rows × 3 phrasings. Each call writes `tool_calls` at once; the manifest only at node end. |
| `interpret` | `graph.py:510` | Coverage rows reported at its start. **Facts only after all extraction and judging.** |
| `sense_check`, `targeted_pulls` | `graph.py:563, 650` | Contradictions, follow-up calls; facts at node end. |
| `distill`, `build`, `gate`, `deliver` | `graph.py:679-796` | Narrative, files, quality gate, delivery. |
| End | `runner.py _end` → `lifecycle.finish` | Tracker sweep, settle, close spend. |

Stop: `POST /v1/research/{job_id}/stop` and `POST /v1/runs/{id}/cancel` end the run CANCELED in the
API transaction. The worker notices within 10 s (`runner.py CANCEL_POLL_S`) and cancels the graph
task; until then it can still pay for calls and write rows on the cancelled run.

## 2. The live-data path

| Data | How the browser gets it | Worst delay today |
| --- | --- | --- |
| Steps | SSE `/v1/runs/{id}/events` (2 s server poll, 60 s cap), then 5 s polling | 2 s, then 5 s |
| Status, tokens | `GET /v1/runs/{id}` every 6 s | 6 s |
| Account counts, findings | Server render; refreshed when the run ends or on Refresh | until the run ends |
| Sheets | Server render, never refreshed | until reload |

There is no response caching anywhere (`Cache-Control: no-store`, `cache: "no-store"`), and no
browser storage of research data: the only `localStorage` use is resumable-upload bookkeeping
(`web/lib/upload-store.ts`). Freshness is limited by when the worker writes and how often the
browser asks, nothing else.

Measured on the local database: every query a one-second change check needs (run status, latest
checkpoint, fact count, call count, token sum, coverage) is indexed by `run_id` and takes
0.5 to 5 ms. A one-second server-side check per open stream is cheap. LISTEN/NOTIFY is not needed
and would be fragile here: the writers are in the worker service, notifications are lost when an
instance scales to zero, and every write path would need a trigger.

## 3. Why research brings back little data

Evidence: the local database's real-provider runs (2026-10-11/12, quidelortho.com; 0 completed,
13 failed, 4 partial, 8 cancelled) and the code as it is today. Staging's database is private and
was not read.

### 3a. Money accounting starves the run

* Every call to a provider without a known price is reserved at `unknown_cost_reserve_usd: 0.25`
  (`config/providers.yaml:42`; scrape, signals, people and org all have `usd_per_credit: null`).
  Local proof: 740 scrapes at exactly $0.25 = $185.
* The run budget is `min(RUN_BUDGET_USD, credits × credit value)` (`runs/lifecycle.py:209`). With
  the default $5 that is **about 20 non-search calls per run**.
* Rows are pulled alphabetically within each step (`graph.py:234 _plan_order`), so the budget goes
  on `basic_fit_check` and `connections_alumni` first. Jobs, leadership, technology and people come
  last and end Blocked `budget_cap`. The owner's skill orders spend the other way: people first,
  then feeds and stack, then media (`config/skill/archive/references/phase1-pull.md`).
* Internal-history rows (`prior_customer_status`, `connections_alumni`) are web-searched and scraped
  although the skill says never to substitute the web for internal history.
* The jobs feed pages 100 records at a time (`providers.yaml:109`; the provider allows 1000), so one
  feed can use the whole $5 in 20 pages.

### 3b. The queries are placeholders

`config/skill/pull_plan.yaml` says so itself: `status: "proposal (tools.md query patterns TODO)"`.
Every row sends `"{account} {topic}"`, `"\"{domain}\" {topic}"` and `"{account} {topic} {year}"`,
where topic is the checklist label. Real queries from a local run:
`quidelortho.com Job board search by lane`, `"quidelortho.com" Podcasts`. There are no lane
keywords (Salesforce, ServiceNow, SAP), no vendor partnership searches and no person-name
searches.

### 3c. Reads stop at the company's own site

`read_page` and `scrape` are refused off the account's domain (`connectors/security.py:84-115`), and
only three own-site reads are made per search (`agents/production/provider.py:75`). So the YouTube
row can never read youtube.com, job boards on Workday or Greenhouse are never reached, and news,
filings and transcripts are only ever snippets. The skill says to read them in full.

### 3d. Paid-for results are thrown away

* **People are never extracted.** `EngineGrading.records_people = False`
  (`agents/production/grading.py:316`) makes the graph skip the people step, while the people rows
  are still pulled and paid for.
* **Feed records fail the off-target guard.** `graph.py:397-412` keeps a public payload only if its
  URL is on the domain or its text names the company. A technology detection or a person record
  fetched *by the company's domain* rarely names it, so it is dropped. Staging logged 599 drops in
  one run.
* **Job postings skip the jobs pipeline.** Workflow v1 wrote 1,953 classified postings per run;
  the research graph writes none to `job_postings`, only what an extractor quotes from 120,000
  character batches.
* **Duplicate payloads.** Feed rows store the same records once per phrasing and `_views` does not
  dedupe them, so the extractor reads each record three times (news six times), wasting tokens.
* **Nothing is statable.** 314 local facts: 0 Confirmed. 80 primary reads were capped to Probable
  because the support judge did not run; cached reads fail "fetched this run".

### 3e. Robustness

* A spending stop in `pull` (`DollarCapReached`) escapes before the manifest is saved: the paid
  pulls are never interpreted.
* Past 8 slices a run ends FAILED `slice_ceiling` with a full refund whatever it holds, and it is
  not auto-resumed. The research graph's model calls go through the Batches API (polled every
  20 s, up to 30 minutes each), so a long `interpret` can use several slices.
* A QUEUED run whose first slice task is lost is never swept (`runs_orphaned` covers RUNNING only).
* The token meter reads `llm_calls`, which the research graph's model path does not write while
  `MODEL_ROUTER` is off; it writes `model_reply_log`. The meter can show 0 for a run that spent.

## 4. What was proposed, and what needs the owner

Safe to change without a spending or policy decision (same or lower spend, follows the skill):

1. One Research page listing every running and recent research, with Start and Stop, and a live
   stream (one-second server check, reconnecting) carrying steps, counts and the newest findings.
2. Facts graded and saved after each source group instead of after the whole `interpret` node.
   Every checklist row belongs to exactly one step and one route need (`extraction.groups`), so
   grading per group sees the same facts per attribute as grading the whole batch.
3. Pull order by the skill's spend priority; internal-history rows Blocked "no internal access"
   instead of web-searched.
4. Payload references deduplicated before extraction.
5. Domain-keyed feed records (news, jobs, technology, org, people by technology) exempt from the
   off-target guard: they are about the account by construction.
6. Query templates per row from the skill instead of the label phrasings.

Needs the owner (money or policy):

* Real per-credit prices for the scrape, signals, people and org providers, or a lower reserve.
* The per-run dollar budget ($5 today).
* Reading off-domain pages in full (news, transcripts, job boards): policy D-30.
* Whether a cached full-page read may count as read this run for Confirmed.
* People recording through the people pipeline, and job postings through the jobs pipeline:
  larger builds.

## 5. What was changed on 2026-10-21 (uncommitted)

Owner decisions taken in the session: the run budget follows the job's credits (ceiling raised
from $5 to $11, the largest job's credits); full reads of other sites' pages a search returned
are allowed. The owner will send the providers' real per-credit prices; until then every call
without a price is still reserved at $0.25.

Research core (`src/agents`):

* Findings are graded and saved per source group, as soon as that group's last extraction call
  returns (`graph.py _extract_steps`); a failing group cancels its siblings (no call keeps paying
  for a node that failed) and raises its own error. Tests: `tests/agents/test_live_findings.py`.
* Each stored payload reaches the extractor once (`graph.py _views`).
* Records pulled by the account's own domain or organization id are never dropped as off-target
  (`guards.ACCOUNT_KEYED_NEEDS`).
* The pull spends in the skill's order: people, then feeds, then searches (`graph._plan_order`).
  Internal-history rows are Blocked `no_internal_access` with no call (tracker reason added; it
  is a stated gap that still lets a run complete). People-step rows are not pulled while their
  findings cannot be saved (`people_not_recorded`).
* Every open-web row sends the skill's own three queries (`config/skill/pull_plan.yaml`
  `queries`, validated at load); youtube searches the open web.
* A web search reads up to two pages it found on other sites (`provider.py
  OFF_SITE_READS_PER_SEARCH`), each host allowed for that one read only, sign-in walls skipped,
  page text kept to 24,000 characters. The call cap is sized from this fan-out (`replica/jobs.py
  job_limits`).
* A Stop reaches the worker within 2 seconds (`workflow/runner.py CANCEL_POLL_S`, was 10).

Research screen (`/research`):

* `GET /v1/research/live` and `GET /v1/research/live/stream` (`api/routes/live.py`,
  `replica/live.py`): every web research running and finished in the last 14 days, the selected
  one in detail (steps, findings by kind, newest findings with their source host, counts). Read
  from the database each second, sent only when it changed; no browser storage. Measured locally:
  a committed fact was on screen 0.24 to 0.72 seconds later. With nothing running the stream
  sends one snapshot and ends (the browser asks again after 5 seconds) so no instance is kept
  awake. Tests: `tests/replica/test_research_live.py`.
* The run page's progress stream no longer falls back to five-second polling for good after its
  first minute (`RunSteps.tsx`), and the server asks the browser to reconnect within a second.

Still open (owner): real provider prices; people recording through the people pipeline; job
postings through the jobs pipeline; whether a cached full-page read may count for Confirmed.
