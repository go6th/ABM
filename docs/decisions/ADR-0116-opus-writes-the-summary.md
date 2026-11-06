# ADR-0116: Opus 5.5 writes the Summary (sense check and distill stay on the planner tier)

- Status: Accepted
- Date: 2026-11-03
- Deciders: product owner (routing defaults of 2026-10-14 and 2026-10-20), recorded by fix prompt C
  task 3 (the prompt named this ADR-0099; that number is taken by skill versioning, so it is 0116)

## Context

Comments in `config/models.yaml`, `llm/choice.py` and `config/model_routing.yaml` said Fable 5.1
does the sense check and distill (the Summary). The code maps `abm_sense_check` and `abm_distill`
to the planner tier, which the active arm routes to `pull_planning` -> Opus 5.5. No ADR records an
owner decision that Fable writes the Summary; only the archived claude.ai skill reference
(`config/skill/archive/references/session-environment.md` 2.4) describes Fable-class analysis on
claude.ai, and the owner moved the analysis default to Opus 5.5 on 2026-10-20 (token review item 4).

## Decision

Keep production as it is: the sense check and distill run on the planner tier (Opus 5.5 by
default; the user's research model when they pick one). The comments in `models.yaml`,
`llm/choice.py` and `llm/route.py` now say so. Rejected: moving the Summary to Fable 5.1 without an
owner decision (2.5x Opus's price).

## Consequences

Moving the Summary to Fable is a separate owner decision: map the two task kinds to an analysis
tier in `llm.route.TASK_KINDS` and drop the G11 declaration (ADR-0117). No replay key changes.
