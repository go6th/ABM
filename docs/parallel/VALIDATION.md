# Validating the 4 hour target (D-44): a protocol, not a claim

The sources: a **4 h hard ceiling** per run (60 slices of 240 s, ARCH); p50 under 10 minutes and
p95 under 45 minutes are **targets to prove** (ARCH); the PRD speaks of one working day (conflict
C7). Nothing below has been measured on real providers.

## What exists today

* A virtual-clock simulation (`simulation-report.md`) of the real scheduler, plan and governor rule
  with stub providers and scripted latencies. It shows that the makespan equals the critical-path
  bound with unlimited capacity and degrades as limits tighten. It is not evidence about real runs.
* Unit events (`work_unit_events`) record queued, started and finished times, lease waits and
  outcomes for every unit, so the measurements below can be computed from any real run
  (`parallel.critical_path.analyse`).

## Measurements to take on real accounts

| Measure | Source | Per |
| --- | --- | --- |
| Unit run time (p50, p95) | `work_unit_events` finished minus started | unit type, provider route |
| Queue wait and lease wait | started minus queued; `lease_wait_s` | provider route |
| Critical path and idle time | `critical_path.analyse` | run |
| Makespan | first queued to last terminal event; run created to terminal state | run |
| Spend | the ledger (`tool_call_events`) and `spend_reports` | run, provider code |
| Rate-limit signals | `provider_limit_events`, connector 429s | provider |

## Staging injector scenario

50 simultaneous runs against stub providers that enforce limits taken from the providers'
contracts (not invented), with latencies sampled from the measured unit durations. Pass when zero
limit violations are recorded, no run exceeds 4 h, and the p50 and p95 targets hold.

## Pass criteria (from the sources)

* Hard: every run reaches a terminal state within 4 h of creation.
* Targets: p50 under 10 minutes, p95 under 45 minutes (C7: to be confirmed by the product owner).

## Inputs the product owner must supply

1. Each provider's real concurrency and rate limits, from its contract or plan page (to replace the
   `ceilings` proposals in `config/parallel.yaml`).
2. Twenty real accounts with consent and credits for the runs (spike 4).
3. The measured unit durations from those runs (they feed `measured_durations`).
4. A decision on C7 (which target applies).

## Results

| Run set | Accounts | p50 | p95 | Max | Over 4 h | Limit violations | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Staging injector (50 runs) |  |  |  |  |  |  | Blocked: provider limits not supplied |
| Real accounts | 0 of 20 |  |  |  |  |  | Blocked: needs real runs and credits (spike 4) |
