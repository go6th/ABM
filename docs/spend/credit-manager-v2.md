# Credit manager v2: limits, live spend, pause and approval, the exhaustion ladder, uploads

Spec: `docs/prompts/build_parallel_credit_v2.md` (M1 to M12). Decisions: ADR-0039 (D-45, D-46),
ADR-0040 (D-47), ADR-0041 (uploads). Every K-rule of Credit cap v1 still holds (its suite runs
unchanged). Config: `config/spend.yaml` section `v2` (every value with a source or a status).

## Soft and hard limits (M1), `src/spend/limits.py`

| Item | Rule |
| --- | --- |
| Hard limit | the approved cap (`account_runs.budget_usd`); the connector's atomic reserve is the only place spend grows and refuses anything beyond it |
| Soft limit | `cap_micros * soft_limit_pct // 100` (integers; `soft_limit_pct` 1 to 99, proposal 80) |
| Crossing | recorded once per cap version (`spend_limit_events.soft_crossed`, partial unique index) inside the ledger's reserve or true-up transaction, with two outbox notifications (operator, tenant) |
| Funding mode | `stopped` after a decline or timeout of this version's pause; `paused` while it is open; `priority_only` at or past soft; else `normal` |

## Live spend (M2), `src/spend/live.py`

One `REPEATABLE READ, READ ONLY` transaction: `committed` (ledger, monotonic), `held` (open
reservations), `cap`, `soft`, `remaining`, the mode, `as_of`, the breakdown by provider code and
category, and the **projection** labelled `estimate` with its base and the count of pending units
with unknown cost. The tenant view is allow-listed: `run_id`, `status` (`within_limit`,
`near_limit`, `waiting_for_approval`, `stopped_at_limit`), `calls_by_category`, `as_of`.

## Pause and approval (M3 to M5, M11), `src/spend/pause.py`

1. The parallel phase drains in-flight units after a cap denial, then opens one request per cap
   version for every unit the cap blocked. Fields (all micro-dollars, each with its base):
   `committed`, `held`, `blocked_known` (upper bounds from the approved breakdown),
   `blocked_unknown_units` x `unknown_hold` (the sample hold, never zero), `needed`, `shortfall`,
   `suggested_cap` (rounded up to the step, at least one step above the cap, at most the ceiling),
   `rows_at_risk`, `room`.
2. Policy declines at once when the run already had `max_pauses_per_run` pauses or the ceiling
   leaves no room.
3. Approve: `POST /v1/runs/{id}/cap/pauses/{request_id}/approve` (admin, a step-up younger than
   `step_up_max_age_minutes`); optional `amount_usd` (a whole number of hundredths of a cent, above
   the cap, not below spend, within the ceiling). A new cap version is appended (`extension`
   body), the old one superseded, the enforcement columns written by `spend.repo`; the run resumes.
4. Decline: `POST .../decline`. Timeout: the approval-timeout task records `timed_out` and resumes
   the run. Both end BUDGET_EXCEEDED with partial results and one settlement. A late approval is
   refused (`late_approval_refused` recorded).
5. Wallet: consulted only when a credit rate is configured (none today, D-04); its refusal declines.

## The exhaustion ladder (M6, M7), `src/spend/exhaustion.py`

Pre-pulled file (consumed by the connector before any paid call) → allowed alternate (tenant policy
`allow_alternate_on_exhaustion`, default true per D-47; translation exists; circuit closed;
labelled with its real route) → Blocked `credits_exhausted` and an upload request. Exhaustion never
touches a cap version or an approval.

## The upload contract and file spec (M8, M9), `src/spend/upload.py`

The spec (stored in `prepulled_intents.spec_json`, hashed) lists `provider_code`, `operation`, the
pending calls (`args_hash`, `args`), `content_types`, `max_bytes`, `account_domain`,
`attestations_required` (`account`, `pull_date`), `max_age_days`, and the parser
(`<provider>.<operation>`, the live normaliser). Operator routes (fail closed until D-33):
`POST /operator/v1/runs/{id}/prepulled/intents` (spec and signed target),
`POST .../prepulled/{intent}/complete` (one pass, typed verdict), `GET .../prepulled/{intent}`.

| Reason code | When |
| --- | --- |
| `intent_not_found`, `intent_expired`, `already_decided` | the intent is unknown to this tenant, past its time, or already has a verdict |
| `not_uploaded`, `too_large`, `wrong_type` | the object is absent, above `upload_max_bytes`, or not an allowed type |
| `scan_failed`, `scan_unavailable` | the scanner found malware, or could not answer (fail closed) |
| `duplicate` | the same sha256 was already accepted for this run |
| `schema_drift`, `empty` | the live parser refused the bytes, or found no record |
| `account_mismatch`, `attestation_missing`, `stale` | a record names another domain; no attestation; the attested pull date is older than allowed |
| `already_registered` | a file already serves this operation (unmatched: stays pending) |

## Notifications (M10), `src/spend/notify.py`

Outbox rows in the event's transaction, `(tenant, dedupe_key)` unique, templates with allow-listed
parameters (tenant templates carry no provider, code, amount or credit), at-least-once relay with
bounded attempts and a recorded `gave_up`. A delivery failure never touches a run.
