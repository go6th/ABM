# ADR-0118: A judge on the generator's model fails the run at its start (G12)

- Status: Accepted
- Date: 2026-11-03
- Deciders: product owner (fix prompt C, task 3, 2026-11-03)

## Context

G12: the support judge and the source re-read never run on the model that extracted the claims.
`grading.integration.judge_profile_ok` returned False when the configured judge and generator tasks
routed to one profile, and grading then capped every claim needing a judge at Probable with the
flag `unjudged`, with no error. `llm.choice.independent_judge` switched a judge to another Claude
model only when the run had a stored model choice.

## Decision

`independent_judge` picks another Claude model of the allow-list or raises
`llm.route.JudgeNotIndependent`. `RunDeps.chosen_model` applies it to every judge call, chosen or
routed. `RunDeps.ensure_independent_judge` runs in `workflow.runner._begin` before the graph, so
before any paid call of the run; a refusal ends the run through the runner's one early-stop path
(`_end_stopped` -> `lifecycle.end_from_active`): FAILED with `refund_all` (the full reservation
released) when the run holds nothing, which is always so at its start. `judge_profile_ok` no
longer returns False: equal profiles mean another Claude model judges; with none it raises.

## Consequences

Claims are never capped for want of judge independence; an unavailable judge (no port, a transport
error, no JSON) still caps and flags `unjudged` as before. The check runs on every slice, so a
config change that removes every other Claude model mid-run ends that run by the same rule
(PARTIAL with what it found, else FAILED with a full refund). No replay key changes with the
shipped configuration (the routed judge, Fable 5.1, already differs from the extractor).
