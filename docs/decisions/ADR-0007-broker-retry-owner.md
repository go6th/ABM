# ADR-0007: one retry owner for broker failures (the graph node)

- Status: Accepted (follows the skill and Architecture 5.3; no change requested)
- Date: 2026-10-02
- Deciders: per skill reference 04

## Context

Skill reference 04 assigns broker 429 and 5xx retries to the LangGraph node
`RetryPolicy` (3 attempts, exponential backoff with jitter, then the fact is Blocked and
the run continues). A second retry loop inside the connector would multiply attempts
(3 x 3), cause retry storms on a struggling provider and duplicate spend.

## Decision

* The connector **never retries**. It classifies each failure into the typed hierarchy
  (`RetryableProviderError`, `RateLimited`, `NonRetryableProviderError`, `AuthError`,
  `CreditsExhausted`, `SchemaDriftError`, `BudgetExceeded`, `CircuitOpen`,
  `SecurityError`, `NotSupportedYet`) using each provider's sourced status table.
* **Pacing is the connector's job.** On 429 (and, for p_signals, its documented 403
  "rate over limit") it sets the provider-wide cooldown in the shared rate store, capped
  at `max_cooldown_s`, then raises `RateLimited`. Every worker waits out the cooldown
  before its next call to that provider. A local wait longer than `max_wait_s` raises
  `RateLimited` instead of sleeping.
* `retry_policy_for(config)` builds the node's `RetryPolicy(retry_on=RetryableProviderError,
  max_attempts=3, backoff from config, jitter=True)`; `fetch_branch` uses it.
* **The node decides when retries are exhausted** with the ledger attempt number carried
  on the error (`blocked_or_retry`): below the budget it re-raises for the policy; at the
  budget, or for any non-retryable class, the branch's coverage row becomes Blocked with
  a neutral reason and the run ends PARTIAL, never FAILED.
* **Fallback on outage** is expressed through the circuit breaker: its failure threshold
  equals the retry budget, so a node that exhausted its retries leaves the circuit open
  and the router's next call for that need falls back (`CircuitOpen`). The router falls
  back only on `CreditsExhausted`, `CircuitOpen` and `NotSupportedYet`; never on
  `SchemaDriftError` or `SecurityError`.

## Consequences

* The node's backoff (1 s, 2 s, 4 s with jitter) and the provider cooldown both apply;
  the cooldown is honoured first because the next attempt's `acquire` waits for it.
  This is coarser than per-response `Retry-After` scheduling in the node, but keeps one
  owner. No change of owner is proposed.
* LangGraph's own retry sleeps are real time; tests cover the decision function and the
  final-attempt path rather than sleeping through backoff.
