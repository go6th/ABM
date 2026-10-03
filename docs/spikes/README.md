# Phase 0 spike records

No Phase 0 spike is represented as complete by the foundation milestone.

| Spike | Evidence required | Current status |
|---|---|---|
| Isolation | 100 concurrent mixed-tenant requests over five DB connections, unset context fails closed, public rendering cannot read private facts, and tenant canary vectors do not cross. | Not run here; Docker database access unavailable. |
| Durability (D-18) | Five mid-fan-out kills, concurrent workers, exactly-once/at-most-once decision, and ledger reconciliation evidence. | Run hermetically 2026-10-03: `tests/system/test_kill_resume.py` (five SIGKILL points between a provider response and its ledger write, resume from the checkpoint; two workers on one run). At most one repeat per key, flagged `possible_duplicate`; ledger sum equals spend; one settlement. Real-queue run pending (V-TASKS-1). |
| Prompt caching | Ten realistic runs and cache ratio from call two through six-minute slices. | Not run; requires approved model/vendor access. |
| Broker economics | 20 real accounts, approved spend cap, terms, cost and coverage. | Not run; requires explicit spend and real-data approval. |
| Autoscaling | 50 synthetic runs against stubs on Cloud Run (ADR-0017 replaces the EKS baseline). | Deferred until the staging project exists. |

Use synthetic fixtures unless the work explicitly requires a human-approved live-service exercise. Record the command, environment, result, date, and reviewer for each completed spike.
