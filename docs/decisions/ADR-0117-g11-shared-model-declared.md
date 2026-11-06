# ADR-0117: G11 fails on a silent shared model; the shared model is declared

- Status: Accepted
- Date: 2026-11-03
- Deciders: product owner (fix prompt C, task 3, 2026-11-03)

## Context

G11 (split mode: extraction agents on the cheaper model, analysis agents on the stronger one) was
tested by `agents.guards.routing_violations`, which compares tier names. It passed although in the
active `automatic` arm the extractor tier (extraction) and the planner tier (the sense check and
distill, ADR-0116) both resolve to Opus 5.5. The prompt suggested `active_arm: single_model`;
renaming the arm would break the arms structure (the arm name is also read by the grading judge's
profile check and the estimate, and a second arm would need a twin), so the declaration is its own
key.

## Decision

`config/models.yaml` declares the equality explicitly: `g11_shared_model: {tiers: [extractor,
planner], adr: docs/decisions/ADR-0117-g11-shared-model-declared.md}`.
`llm.route.split_mode_problem` refuses equal extractor and planner models unless that declaration
names exactly that tier pair and an ADR path; `platform.llm.load_routes` raises at startup on a
silent equality, and `test_g11_extraction_and_analysis_agents_differ_or_one_model_is_declared`
asserts the routed models differ or the declaration's ADR file exists. The tier-name check stays.

## Consequences

A change that routes the two tiers to different models makes the block unnecessary (remove it); a
change that makes them equal without it stops the service from starting. A user's research model
serves both tiers (both are research-role tiers), so a picked research model always shares one
model; the declaration covers that too. No replay key changes.
