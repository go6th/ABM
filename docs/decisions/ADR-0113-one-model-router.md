# ADR-0113: One model router; the dormant D-48 Router is removed

- Status: Accepted
- Date: 2026-11-03
- Deciders: product owner (fix prompt C, task 1, 2026-11-03)
- Supersedes: ADR-0042 (fallback as selection), ADR-0045 (routing governance)

## Context

Three implementations decided task kind -> model. The live one: `RunDeps.model` -> `_legacy_model`
-> `platform.llm.ChoiceLlm`, with `llm/tasks.py::LEGACY_TIER` and `platform/llm.py::TIER_TASK` over
`config/models.yaml`. The second, `llm/port.py::Router` with `llm/routing.py::select` over
`config/model_routing.yaml`, ran only with `MODEL_ROUTER=on`, refused to start without a benchmark
report that never existed, and Terraform never set the variable; `docs/routing/DECISIONS.md` lists
every `model_routing.yaml` version as proposal-unapplied with no benchmark and no sign-off, and
BUILD_LOG plans no release of it. The third, `account_intelligence/model_routing.py` over the
`routing:` block of `models.yaml`, had no importer. The live path still read `model_routing.yaml`
for each task kind's token limit and cacheability. The admin "model split" that the Router honoured
(`with_model_split`) had no live effect: its column was dropped in migration 0064.

## Decision

Keep one routing function. `src/llm/route.py` holds the one typed table: `TASK_KINDS` (task kind
-> tier, token limit, cacheable; the values moved unchanged from `model_routing.yaml`) and `TIERS`
(tier -> `models.yaml` task, role, whether the user's effort applies; the default effort comes from
`task_settings`). `RunDeps.resolve_model` (was `_legacy_model`) is the only function that resolves
task kind -> tier -> model and effort and calls `ChoiceLlm`; `RunDeps.model` delegates to it and
`agents.production.wiring` reaches the port only through `RunDeps.model`. Deleted: the Router and
its selection, config loader, health and call-record stores, prompt-prefix, budget and model types,
the scripted adapter, the `AnthropicAdapter` class (its `map_error` stays: the live path uses it),
`llm/tasks.py`, `config/model_routing.yaml`, `model_routing.py`, the `routing:` block, the
duplicated `research_tiers`/`analysis_tiers` lists of `choices`, `MODEL_ROUTER` (now listed as
retired in `docs/ENVIRONMENT.md`), the console's `model_router_on` field,
`scripts/check_routing_governance.py` and its CI steps (ci.yml, eval.yml). Six task kinds were
registered with zero call sites and are gone: `synthesise_hypotheses`, `write_narrative`,
`narrative_reread`, `file_summarize_section`, `file_answer_question` (the five the audit named) and
`distill` (the ABM distill agent calls `abm_distill`; "distill" in `src/` is a graph node name).
`test_every_registered_task_kind_has_a_call_site` fails when a registered kind has no call site.
The governance doc's rule ("a routing change needs a benchmark id and a passing eval gate", R12,
D-51) is archived here: a routing change is now a change to `src/llm/` or `config/models.yaml`,
which the eval workflow still gates as a model change. Rejected: keeping the Router behind its flag
(two sources of truth, rules that never ran in production).

## Consequences

The Router's data-class refusal (tenant or private data only to verified zero-retention models,
R8) never ran on the live path and is not reimplemented: an owner decision. `llm_calls`,
`llm_call_attempts` and `llm_candidate_health` stay in the database unwritten (no migration);
`views.token_use` still reads `llm_calls` (zero rows). `llm/cost.py` keeps its name but is now the
one price table (ADR-0115). Replay keys do not change from this ADR: every surviving task kind
keeps its tier, token limit and cacheability exactly.
