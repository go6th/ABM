# Runbook: a provider lease looks stuck

**Symptom.** Units of one provider stay `queued` or `requeued` with growing `lease_wait_s`;
`governor_active(provider, now())` stays at the limit.

**Why it cannot be permanent.** Every lease has a TTL; a holder that dies stops heart-beating and
its lease is reclaimed (marked `expired`, never deleted) by the next acquisition after `expires_at`
(`test_governor_reclaims_a_dead_holders_lease`).

**First checks (operator role; identifiers only).**
```sql
SELECT governor_active('p_xxx', now());
SELECT current_limit, ceiling, last_change_at, last_rate_limited_at FROM provider_limits
 WHERE provider_code = 'p_xxx';
SELECT event, old_limit, new_limit, at FROM provider_limit_events
 WHERE provider_code = 'p_xxx' ORDER BY id DESC LIMIT 20;
```
Run the lease query per affected tenant through its own tenant context (RLS):
`SELECT holder, acquired_at, heartbeat_at, expires_at FROM provider_leases WHERE released_at IS NULL`.

**Mitigation.** Wait one TTL (`config/parallel.yaml` `lease_ttl_s`). If the limit was halved by a
rate-limit storm, see `runbook-limit-storm.md`. Never update or delete lease rows by hand.

**Verification.** `governor_active` drops below the limit and the units start.
