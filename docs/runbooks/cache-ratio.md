# Runbook: Cache-read ratio below 70%

Owner: TODO (placeholder, docs/oncall/rotation.md)

## Symptom
Cacheable model calls read less than 70% of their input from the cache.

## Impact
Higher model cost.

## First checks
Run `queries/cache_ratio.sql` (read only; never read tenant content, never bypass RLS, never run an unregistered
script). Open the run timeline and traces by run id when one is named.

Without database access, `python scripts/cache_report.py --freshness 1d` reads the `llm.usage`
(research, one line per call) and `ask.route` (Ask) log lines and prints the read share per
surface, tier and model. A research call's `kind` says how it met the cache: `lead` warmed a
prefix, `follow` waited for a leader and read it, `warm` found it cached, `none` was too short to
cache (the API caches nothing under the model's `min_cache_tokens`, models.yaml).

The alert reads the `cache_read_ratio` gauge (per research tier, rolling one hour, set by
`platform.llm` on every call since 2026-10-21) and `llm_tokens_total` by `token_kind`.

## Mitigation
If the warm gate itself misbehaves (calls waiting, a run slower than before), switch it off with
`prompt_cache_gate.enabled: false` in config/models.yaml and redeploy the worker: every call is
then sent as before the gate (logged as `kind` `off`). Otherwise look for a prompt or prefix
change (a timestamp or id in the prefix); revert it. Shared text goes first: `SystemPrompt` parts (platform/prompt_cache.py) are sent most widely shared first, so a
change to a leading part (the session document, the skill) is a miss for every task kind.

## Escalation
Page or ticket per docs/oncall/severity.md; escalate to the owner above.

## Verification
Ratio above 70% for an hour.

## Follow-up
A blameless post-incident review (docs/postmortems/TEMPLATE.md) for every page.
