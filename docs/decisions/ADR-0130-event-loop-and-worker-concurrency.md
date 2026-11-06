# ADR-0130: nothing blocks the event loop; worker concurrency 1 until the loop test holds

- Status: Accepted (code); Terraform planned, NOT applied
- Date: 2026-11-03
- Deciders: owner (fix prompt E, task 3)

## Context

The worker was killed by its liveness probe at least four times by a blocked event loop (YAML
parse per table cell, certificate fetch, archive zip, ledger parse). `obs.stalldump` names the
code after the fact; nothing prevented the next one. With `concurrency = 4`, one blocked loop
stalls four requests (run slices, document parts, jobs) and their lease heartbeats at once.

## Decision

1. **Audit and fixes**: `docs/audits/event-loop.md` lists every site with file:line and its fix
   (thread, file-state cache, or startup warm-up `app.warm.warm_caches`, run in a thread from the
   `api.main` lifespan). The critical one was the PDF export (LibreOffice on the loop); the
   largest measured in a research was the build node's name scan (0.9 s).
2. **Test**: `tests/system/test_event_loop.py` runs a hermetic company research in-process with
   asyncio debug mode and `loop.slow_callback_duration` at the threshold, times every callback in
   wall time and in the loop thread's CPU time, and fails when one spends more than
   `EVENT_LOOP_SLOW_CALLBACK_S` (0.05 s) of loop CPU. Excluded, with reasons in the test:
   garbage collection (measured with `gc.callbacks`, subtracted, reported), GIL starvation by
   offloaded threads (tolerated up to `EVENT_LOOP_STARVED_S`, 1 s), FastAPI's one-time lazy route
   setup, asyncio debug mode's own bookkeeping. A failure names "async function -> innermost src
   frame" from a sampler thread. Green three runs in a row after the fixes.
3. **Worker concurrency 4 -> 1** (`infra/stack/main.tf` `local.size.worker.concurrency`, both
   environments; plan only). The queues' `max_concurrent_dispatches` now derive from
   `worker_slots = scaling.worker.max x concurrency` (runs half, documents and jobs a quarter
   each, at least one): with concurrency 4 they are exactly the old values.

## Trade-off

- One request per instance: a blocked loop stalls one slice, never its neighbours' heartbeats
  and leases. Each run slice gets the instance's 4 vCPU.
- Capacity now comes from instances. At staging's `scaling.worker.max = 3` the worker holds 3
  requests at once (was 12): runs 1, documents 1, jobs 1. To keep 12 slots set
  `scaling.worker.max = 12` in `terraform.tfvars` (owner input; not changed here). Cost: the
  worker scales to zero and bills per instance-second while busy; at concurrency 1 the same work
  runs on up to 4x the instances (4 vCPU / 12 GiB each, plus the clamd sidecar), so peak cost
  per concurrent slice rises up to 4x while total vCPU-seconds for serial work stay about the
  same. Cold starts are more frequent.
- Database: more instances means more pools (`pool size 5 + overflow 5` each); see ADR-0131's
  sizing rule.

## Rule

Back to concurrency 4 after `tests/system/test_event_loop.py` has been green in CI for two
releases (and no liveness kill in between).

## Not verified

`terraform plan` was not run in full for this ADR (see the task report); `terraform validate`
passes. The audit's lower-severity sites (connector `parse`, per-call `httpx.AsyncClient`, small
uncached YAML reads) are listed but not fixed.
