# Every cap in the project (checked 2026-10-30)

Read from the code and config, not from memory. "Off" means `RESEARCH_CAPS=off`, which staging has used
since 2026-10-21 (`infra/envs/staging/main.tf`, default `off`). The base Terraform default is `on`, so a
new environment starts with every cap applied.

## 1. The one switch

`RESEARCH_CAPS=off` (`dev_limits.research_uncapped`) lifts the caps in section 2. `DEV_UNCAPPED=1` does the
same on a developer machine only. `TOKEN_GUARD=on` keeps the token pause while the caps are off.

## 2. Caps the switch lifts

| Cap | Caps on | Where |
|---|---|---|
| Run budget in dollars | the job's credits times $0.05, never above `max_budget_usd` (now $12.50) | `runs/lifecycle.py`, `config/workflow_v1.yaml` |
| Provider calls per run | 60 by default; a research job sizes it from its plan (`replica/jobs.py job_limits`) | `lifecycle.py`, `connectors/budget.py` |
| Credit pool and researches per user | the client's pool; researches per user from client settings | `replica/credits.py`, `replica/limits.py` |
| Plan limits on accounts, runs, refreshes | pilot 10 accounts, 20 runs, 0 refreshes; standard 50, 100, 200 (a product proposal, marked TODO) | `metering/limits.py`, `config/plans.yaml` |
| Slices per run | 16 (was 8; see section 4) | `config/workflow_v1.yaml` |
| Provider timeouts, retries, polls, bytes | the provider defaults; off raises them to 15 to 120 minutes, 10 retries, 200 MB | `connectors/settings.py` |
| Breadth: pages per search, postings read, people, profiles, posts, technologies per lane | fixed counts (own pages per search, off-site reads per search, `max_page_reads`, `max_extractor_calls`, people caps) | `agents/production/provider.py`, `jobs/config.py`, `people/config.py` |
| Per-agent token and call budgets | the contract budgets | `agents/contracts.py` |
| Characters kept from a long page | `MAX_READ_CHARS`; off keeps up to 120,000 | `agents/production/provider.py` |
| Credit-cap gate and spend limits | the cap gate, the platform dollar cap, the repeat-call limit | `connectors/spend_guard.py` |

## 3. Limits that stay on with the caps off

| Limit | Value | Purpose |
|---|---|---|
| Token guard | pause at 3 times the pre-run token estimate (only when `TOKEN_GUARD=on` while caps are off) | stop a runaway model loop |
| Empty slices | 3 in a row end the run PARTIAL `no_progress` | the 20-hour stall of 2026-10-26 |
| Slice length | 1650 s, inside the 1800 s Cloud Tasks deadline | one delivery must finish |
| Row ceilings | 540 s per pulled row; 300 s per single page read, search or lookup in the jobs and people pipelines | one hung call must not hold a slice |
| Lease | renewed every 20 s; taken over after 90 s without a renewal | a dead worker is replaced inside the retry window |
| Automatic resumes | 5 per chain, within 24 hours | a crash loop cannot spend for ever |
| Queue attempts | 8 on the `runs` queue (set in Terraform, not yet applied) | retries after a worker dies |
| Rate limits per user | upload 120 then 30 a minute; export 30 then 3 a minute; download everything 10 an hour; archive 120 a minute | abuse and cost |
| Output sizes | 2,000 archived copies in the zip; Excel's 32,767-character cell; 1,000 characters for stack names | file validity |

## 4. Findings and what I changed

1. **Slice ceiling was 8 (3 h 40 min).** The staging runs in the notes took 20 hours on a large account and
   the reading step alone took 75 minutes. With the caps on, a large account would end PARTIAL
   `slice_ceiling` with files held. **Changed to 16 slices (7 h 20 min).** The empty-slice rule, the token
   guard and the budget still stop a run that goes nowhere.
2. **The public run of a file analysis had a 60-call cap.** It was never sized from the plan, so with the
   caps on it stopped after a few rows. **Fixed** (`api/routes/file_runs.py`), with an assertion in
   `tests/system/test_offline_end_to_end.py`. The end-to-end test that failed one run in three now passes.
3. **Full research is priced at 250 credits ($12.50)** to cover the wider web search, which adds about $1.50.
   The floor for a repeat or resumed run is half of that (125).
4. **Other run creators keep the 60-call default** by design: a plain run through `POST /v1/runs` and a file
   analysis without public sources. Neither is the research graph, so 60 is not a fault, but check that
   before putting such a run behind the caps.

## 5. Owner decisions on caps

- **When to switch the caps back on.** Staging runs with them off. Production must not go live with
  `RESEARCH_CAPS=off` unless you accept an unlimited spend per research. The estimate screen and the 3 times
  token guard are your only brakes while they are off.
- **Plan limits are placeholders.** The pilot plan allows 0 refreshes and 20 runs a month. These numbers say
  "needs product owner" in the file. Decide them before the caps are on for a client.
- **Run budget of the smaller jobs.** Jobs cost 20 credits and people 60, which is $1 and $3. The call cap
  is sized from the plan but the dollar budget is the job's credits, so a jobs refresh on a very large
  account can hit the dollar budget first. Measure after the first real runs.
- **Feed pagination** is deliberately not bounded by a ceiling, so a large account's feed takes about 20
  minutes and resumes across slices. That is correct but slow; the scrape provider's plan sets the speed.
