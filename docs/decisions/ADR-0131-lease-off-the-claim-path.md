# ADR-0131: the ledger claim no longer renews the run lease; claims are bounded per worker

- Status: Accepted (code); Terraform planned, NOT applied
- Date: 2026-11-03
- Deciders: owner (fix prompt E, task 4)

## Context

Every paid call runs `Ledger.claim` in one transaction that also upserted `run_leases`
(`INSERT ... ON CONFLICT DO UPDATE`: a new row version and WAL for every claim, on the one row
every claim of the run also locks). With fan-out (search 8, scrape 4) on the 1-core Cloud SQL
instance this saturated the database once (2026-11-01).

## Decision

- **Lease renewal belongs to the heartbeat.** `workflow.runner._drive` already renews the lease
  every `lease_heartbeat_s` (20 s, `config/providers.yaml`) on its own short transaction
  (`Ledger.acquire_lease`). `claim` and `is_replayable` now call `Ledger._held_lease`: `SELECT
  owner, epoch, expires_at FROM run_leases WHERE run_id = :r FOR UPDATE` (the row lock stays the
  run mutex that serialises the run's ledger transactions and its budget reservation). When the
  lease is this worker's and live, its epoch is used and nothing is written. Any other case (no
  lease yet, expired, another owner) goes through the old upsert unchanged, so a lease another
  live worker holds still raises `LeaseUnavailable` and a stale one may still be taken.
- **Lease loss is still detected**: at the next claim (`LeaseUnavailable`) and at the next
  heartbeat (`_renew_lease` -> `LeaseLost`, the slice stops). Tests:
  `tests/connectors/test_ledger_budget.py::test_a_claim_on_a_held_lease_does_not_renew_it`,
  `::test_a_worker_whose_lease_was_taken_cannot_claim`; the runner's
  `test_the_lease_is_renewed_while_a_node_runs_and_a_lost_lease_stops_the_slice` stays green.
- **Bounded claims**: at most `config/parallel.yaml` `worker.max_in_flight_claims` (4) claim
  transactions of one worker instance are open at once (an `asyncio.Semaphore` per event loop, in
  `Ledger`; wired by `connectors.factory`). Test:
  `::test_claims_in_flight_are_bounded_per_worker`.
- **Alerts (Terraform, plan only)**: `infra/modules/observability/platform.tf` gains a
  `saturation` entry in the Cloud SQL alert set: CPU > 80 % for 5 minutes (60 s alignment),
  paging; the existing 15-minute CPU alert stays. `infra/envs/staging/main.tf`
  `workload_alerts_enabled` defaults to `true` (the untracked `terraform.tfvars` already sets it
  true, so the staging plan shows no change from this default alone).

## Sizing rule

Database cores >= ceil(max_in_flight_claims / 4) per worker instance claiming at full fan-out,
summed over the instances that can fan out at once. With `max_in_flight_claims = 4` the current
`db-custom-1-3840` covers one instance. Proposed in a comment on `db_tier` in
`infra/envs/staging/main.tf`: `db-custom-2-7680` (2 vCPU, 7.5 GiB) once more than one worker
instance fans out at once (ADR-0130 moves the worker to concurrency 1, so concurrent runs are on
separate instances) or the limit goes to 8. Not applied.

## Consequences

- A claim no longer moves `run_leases.heartbeat_at`; a worker is "alive" by its heartbeat only.
  A live worker whose loop is blocked longer than `lease_stale_after_s` (90 s) can lose its lease
  to a retry, as before (claims could not run while the loop was blocked either).
