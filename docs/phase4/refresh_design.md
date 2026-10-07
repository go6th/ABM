# Refresh design (Phase 4 WS1, WS2; RF1 to RF8)

## Slots
One row per (account, kind, period) in `refresh_slots`, unique: a duplicate tick, a duplicate
delivery or a manual overlap finds the same row. Periods: ISO week for probes, month for escalated
refreshes. Workers claim due slots with `FOR UPDATE SKIP LOCKED` and a conditional update; jitter is
a stable offset inside the hour per (account, kind, period). After an outage, catch-up runs oldest
due first, priority A before others on ties, at most 50 a tick and 5 per tenant (proposals). A slot
claimed by a dead worker is recovered by lease: `done` when its run exists (unique per slot),
otherwise released once.

## Probe
`refresh.probe.run_probe`, with no transaction across a provider call:
1. Snapshot the facts the account has already been told about (the brief's, and those named by an
   earlier delta). Facts left by a probe that crashed before its delta stay new for the retry.
2. Per monitor kind: reserve monitoring allowance (atomic), poll through the monitor port. A failed,
   rate-limited or unconfigured kind is Blocked with a reason; throttled accounts make no call.
3. Grade each observation through the grading port (production: the tool-less extractor and the
   grading engine); unverifiable candidates are dropped and counted.
4. One transaction: debounce (absent twice before REMOVED; blocked never counts), expire removed
   items into `signal_history`, compute the delta (new, expired, regraded, count changes with base),
   score it, decide escalation (pure, stored with its inputs), record the run, observations, delta,
   signal events and alerts.

## Full refresh (escalated)
A pending monthly `refresh` slot. The planner re-runs High rows (PRD 4.5) and rows past their shelf
life, carries the rest forward visibly "not due" with the run they were last checked in, and treats
rows the probe pulled this period as inherited (never pulled twice; C27).

## Never delete
Expired signals keep their fact with `superseded_at` (set once, through the grading repo) and an
insert-only history row with the reason and run. Source codes come from the single allocator and
are stable across cycles (property test).
