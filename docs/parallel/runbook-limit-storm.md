# Runbook: rate-limit storm on a provider

**Symptom.** Many 429s from one provider; `provider_limit_events` shows `decrease` events; units of
that provider queue longer.

**What the system does.** The connector sets the provider cooldown (pacing); the governor halves the
limit (never below 1) at most once per window; the node RetryPolicy is the only retry owner; after
a full clean window the limit grows by one, never above the ceiling.

**First checks.**
```sql
SELECT event, old_limit, new_limit, reason, at FROM provider_limit_events
 WHERE provider_code = 'p_xxx' ORDER BY id DESC LIMIT 50;
SELECT current_limit, ceiling FROM provider_limits WHERE provider_code = 'p_xxx';
```

**Mitigation.** If the ceiling is above the provider's real limit, lower `ceilings.p_xxx` in
`config/parallel.yaml` with the source of the real limit, deploy, and the governor applies it
(`governor_configure`). Do not raise a ceiling without a source.
