# ADR-0114: The user's effort no longer reaches extraction or classification

- Status: Accepted
- Date: 2026-11-03
- Deciders: product owner (fix prompt C, task 1, 2026-11-03)

## Context

`RunDeps._effort` gave the effort a user picked for a role (medium or high, owner decision
2026-10-20) to every tier of the role, so a run that stored "medium" paid extraction and
classification at medium instead of their configured `task_settings` effort (low), on Opus 5.5.

## Decision

`llm.route.TIERS` marks which tiers take the user's effort: the judgment tiers of each role
(planner, which runs planning, the sense check and distill; synthesis, narrative and the judge).
The extractor and classifier tiers keep their configured effort (low) unless the agent's entry in
`config/agents.yaml` names an `effort` (validated against the known levels; none is set today),
which `RunDepsModel` passes through `RunDeps.model(effort=...)`. Read literally, "the user's effort
applies to the analysis role only" would make the research-effort picker a no-op (every research
tier would ignore it); the planner tier keeps the research effort instead, and the report says so.

## Consequences

Extraction and classification calls of runs with a stored effort get a different reply-cache key
(`key_tier` loses its `~medium`/`~high` suffix): `extract_facts`, `file_extract_facts`,
`classify_person`, `classify_posting`, `abm_setup`, `abm_pass1_extract`, `abm_pass2_extract`,
`abm_people_extract`. A resumed run re-asks those calls once (accepted; REPLAY_KEY_VERSION is
prompt E's). Tested by `test_the_users_effort_never_reaches_extraction_or_classification`.
