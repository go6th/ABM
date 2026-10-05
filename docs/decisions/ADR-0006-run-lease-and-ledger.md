# ADR-0006: run lease and insert-only intent log for paid calls

- Status: Proposed
- Date: 2026-10-02
- Deciders: TODO (product owner)

## Context

Known defect 3 (skill reference 13): the starter `ledgered_call` read the ledger, called
the provider, then wrote the ledger in a second transaction. Two concurrent identical
calls both paid, and a crash between the response and the write could repeat the call
an unbounded number of times. WP 1.4 asks for a per-run lease (row or advisory lock),
an intent row before the paid call, reconciliation of stale intents, and proof by
tests (concurrent calls, two workers, kill between response and ledger write).

Constraints: the app role is not the owner and has no BYPASSRLS; the tenant context is
transaction-local (`tenant_txn`); ledgers are insert-only for the app role (skill rule 7,
reference 05); migrations are expand-only; tests must control time.

## Decision

1. **Lease row, not an advisory lock.** Table `run_leases(run_id, owner, epoch,
   expires_at, heartbeat_at)` with forced RLS. Every claim upserts it in the same
   transaction: insert, or update when the owner is the same or the lease has expired
   (`expires_at <= now`). A different live owner gets no row and `LeaseUnavailable`;
   the worker yields and the orchestrator reschedules. `epoch` increases on every change
   of owner and is stored on every intent event.
   *Why not an advisory lock:* a session-level lock needs a connection held for the
   whole slice, which conflicts with transaction-local tenant context and pooled
   sessions (reference 03 pooling trap); a transaction-level lock cannot span the
   provider call. A row is visible, expires on its own, survives a dead connection,
   and its clock can be injected for tests.
2. **The lease row lock is the run mutex.** The upsert (claim) and a `SELECT ... FOR
   UPDATE` (finalise) serialise all ledger transactions of one run, so the replay check,
   the attempt decision, the budget reservation and the intent insert are atomic.
3. **Insert-only intent log** `tool_call_events(run_id, args_hash, attempt, event)` with
   events `started` (written with the budget reservation **before** the provider is
   called), `submitted` (async provider job id), and terminal `done`, `failed`,
   `abandoned`. `tool_calls` keeps one row per `(run_id, args_hash)` for a successful
   result and stays insert-only. No grant allows UPDATE or DELETE on either.
4. **Claim rules.** A `tool_calls` row means replay. An open `started` attempt by the same
   owner and epoch means an identical call is in flight in this worker: wait (bounded),
   then replay. An open attempt from another owner or epoch is stale: close it as
   `abandoned` (its reservation stays charged, `possible_duplicate = true`) and start a
   new attempt flagged `possible_duplicate`, or, if a provider job id was recorded, poll
   that job instead (no duplicate charge; sourced for p_org).
5. **Money moves with events.** Reserve on `started`, true up on the terminal event, in
   the same transaction. A late terminal event of an abandoned attempt records the
   result but moves no money. Hence `account_runs.spent_usd` equals `ledger_sum`.

## Consequences

* Guaranteed and tested: pull-once replay; 50 identical concurrent calls make one
  provider call; two workers on one run never both pay; a kill between response and
  ledger write (real subprocess, `os._exit`) repeats at most one call, flagged; a kill
  mid-pagination resumes from stored pages; spend equals the ledger after every scenario.
* Not guaranteed: exactly-once side effects at the provider. A crash inside the window
  costs at most one repeat per crash, flagged `possible_duplicate` for invoice
  reconciliation. None of the sourced providers documents an idempotency key for the
  operations used (p_scrape's `x-request-id` is documented only for another feature).
* A crashed worker blocks its run's paid calls until `lease_ttl_s` (300 s, longer than a
  240 s slice) passes. The runner should call `release_lease` at slice end.
* The lease is per run, so parallel branches of one run inside one worker share it; a
  second worker process on the same run is refused, not queued.
