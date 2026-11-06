# 04 Model routing

Verified against the code on 2026-11-03 (working tree of `feat/final-product`, uncommitted changes included).

See also: [index](README.md) · [01 architecture](01-architecture-and-planes.md) · [02 request path](02-request-path-and-auth.md) · [03 run lifecycle](03-research-run-lifecycle.md) · [05 spend and caps](05-spend-and-caps.md) · [06 data and isolation](06-data-and-isolation.md) · [07 documents](07-documents-and-sandbox.md) · [08 exports and gate](08-exports-and-gate.md) · [09 ops](09-ops.md) · [10 known gaps](10-known-gaps.md)

## One router (ADR-0113)

Every research model call names a **task kind** and goes through `RunDeps.model` -> `RunDeps.resolve_model` -> the live port `platform.llm.ChoiceLlm` (`src/llm/route.py:1-7`, `src/account_intelligence/workflow/deps.py:115-145`, `:241-358`). The ABM agents reach it only through `RunDepsModel.call` -> `deps.model` (`src/agents/production/wiring.py`, class `RunDepsModel`).

The deleted pieces are gone from disk: `config/model_routing.yaml`, `src/llm/port.py`, `src/llm/routing.py`, `src/llm/tasks.py`, `src/account_intelligence/model_routing.py` (checked with `ls`); no `_legacy_model` or `MODEL_ROUTER` remains in `src/` (grep).

### The table: `src/llm/route.py`

- `TIERS` maps tier -> (`models.yaml` task, role, takes the user's effort) (`src/llm/route.py:83-90`): planner -> `pull_planning` (research, yes); extractor -> `extraction` (research, **no**); classifier -> `classification` (research, **no**); synthesis, narrative -> analysis (yes); judge -> `verification` (analysis, yes).
- `TASK_KINDS` maps 15 task kinds -> (tier, `max_tokens`, cacheable) (`src/llm/route.py:92-115`). Workflow v1: `plan_gaps`, `extract_facts`, `lead_queries`, `source_reread`; shared: `classify_posting`, `second_pass_posting`, `classify_person`, `support_judge`; file mode: `file_extract_facts`; ABM: `abm_setup`, `abm_pass1_extract`, `abm_pass2_extract`, `abm_people_extract`, `abm_sense_check`, `abm_distill`.
- `route(task_kind)` gives tier, task, default effort (from `task_settings`), role, limit; an unknown kind raises `UnknownTaskKind` (`src/llm/route.py:131-153`).

### `resolve_model`, step by step (`src/account_intelligence/workflow/deps.py:241-358`)

1. `route(task_kind)` -> tier, token limit, cacheable (`:278-280`).
2. `chosen_model(tier, override)`: an override (fallback) wins; else the user's picked model for the role, if it differs from the role default; else None, meaning the active arm's route (`:178-215`). For the judge tier it then enforces G12 (below).
3. The data-class guard (ADR-0133) runs **before** any reuse lookup or paid call (`:282-283`).
4. Effort: an agent's own `effort` from `config/agents.yaml`, else the user's effort only where `TIERS` allows it (`:224-230`, `src/llm/choice.py:139-148`). A non-default effort is part of the key (`key_tier~effort`, `:285-288`).
5. Not cacheable: spend check, call, log (`:291-305`). Cacheable: in-run replay by request digest in the run's payloads (`:306-314`), then the cross-run reply cache keyed by `reply_key` (`:315-335`), then spend check (`_spend_check`: token guard then dollar cap, `:360-371`) and the paid call (`:336-358`).

### Models and arms: `config/models.yaml`

- Allow-list of six models (`config/models.yaml:9-15`). Profiles name provider + model (`:26-40`).
- `active_arm: automatic` (`config/models.yaml:76`): `pull_planning`, `extraction`, `classification` -> Opus 5.5; `synthesis`, `narrative`, `verification` -> Fable 5.1 (`:43-54`). `cost_saving` (Sonnet extraction) exists but is not active (`:55-67`; the owner reverted it, `:69-75`).
- `task_settings` default efforts: planning medium, extraction/classification/verification low, synthesis/narrative medium (`config/models.yaml:99-105`).
- `platform.llm.load_routes` resolves every tier through the active arm, refuses a model off the allow-list or an unknown effort, and enforces G11 at startup (`src/account_intelligence/platform/llm.py:79-120`).

## ADR-0114: extraction keeps its effort

The user's effort never reaches the extractor or classifier tiers (`src/llm/route.py:85-86`, `user_effort_applies` at `:161-164`, `effort_for_tier` returns None for them, `src/llm/choice.py:145-146`). An agent may set its own `effort` in `config/agents.yaml` (`config/agents.yaml:3-6`); none is set today (`config/agents.yaml:6`). Note `classify_person` is on the extractor tier (`src/llm/route.py:103`).

## ADR-0115: one price table

`llm/cost.py` reads only `models.<id>.pricing`, four required prices per priced model (`src/llm/cost.py:1-7`, `:18-23`, `:46-63`); `price_of` returns None, never zero, for an unpriced model (`:66-68`). Consumers: `reply_cache.price` (`src/llm/reply_cache.py:260-267`), the chat registry, which skips models without a price and so cannot list them (`src/account_intelligence/chat/registry.py:16`, `:128-147`), and the file-mode estimate (`src/documents/estimate.py:30-37`). Test: `tests/llm/test_prices.py`.

## ADR-0116: Opus writes the Summary

`abm_sense_check` and `abm_distill` are on the planner tier (`src/llm/route.py:111-114`), which the active arm routes to Opus 5.5 (`config/models.yaml:49`). Moving the Summary to Fable needs a new tier mapping and removing the G11 declaration.

## G11 (ADR-0117): extraction vs analysis agents

`split_mode_problem` refuses equal extractor and planner models unless `models.yaml` declares `g11_shared_model {tiers: [extractor, planner], adr: docs/decisions/ADR-....md}` (`src/llm/route.py:167-190`). The declaration exists (`config/models.yaml:85-87`) because both tiers are Opus 5.5 in the active arm. `load_routes` raises on a silent equality (`src/account_intelligence/platform/llm.py:112-119`). Test: `tests/agents/test_registry.py`.

## G12 (ADR-0118): the judge is never the generator's model

G12 is judge independence: the support judge and source re-read never run on the extractor's model.
- `chosen_model("judge")` compares the judge with the extractor (chosen or routed); when equal it switches to `independent_judge`, the first other Claude model of the allow-list, or raises `JudgeNotIndependent` (`src/account_intelligence/workflow/deps.py:204-215`, `src/llm/choice.py:201-210`, `src/llm/route.py:39-41`).
- `ensure_independent_judge` (`src/account_intelligence/workflow/deps.py:217-222`) runs in the runner's `_begin` before the graph, so a refusal ends the run FAILED with full refund before any paid call (`runner.py`, `_begin` -> `_end_stopped("judge_not_independent")`).
- `judge_profile_ok` no longer returns False; it raises when no independent judge exists (`src/account_intelligence/grading/integration.py:85-99`).
- With the shipped config the routed judge (Fable 5.1) already differs from the extractor (Opus 5.5) (`config/models.yaml:50`, `:54`).

## ADR-0119: Kimi/Grok fallback is a billing event

In `RunDepsModel.call` a step whose chosen model is not Anthropic gets 2 attempts, then `_fallback` switches to `claude_default(role)` for 2 more; `_settle_fallback` records `discarded_usd`/`extra_usd` on the run and writes one idempotent `usage_events` row of kind `model_fallback` with a `detail` object (`src/agents/production/wiring.py`, `RunDepsModel.call`, `_fallback`, `_settle_fallback`; `src/llm/choice.py:233-236`; `migrations/0100_model_fallback_usage_event.sql:16-17`; `src/metering/events.py:36-54`). `GET /v1/runs/{id}` returns `token_use.fallbacks` (`src/account_intelligence/api/contract.py:1064`, `:1098`).

## ADR-0120: payload fences and the prompt guard

`RunDepsModel.call` sends `fence_payloads(payload)`: every page text inside `<payload id="...">...</payload>`, with `<payload`/`</payload` in the page neutralised (`src/account_intelligence/agents/fence.py:1-8`, `:24-27`, `:37`). The Pass 1, Pass 2 and People prompts name the element (`src/agents/pass1/prompt.md:5`, `src/agents/pass2/prompt.md:5`, `src/agents/people/prompt.md:5`). The second-pass prompt is now `posting_second_pass.v2.md` with the restored guard (`src/account_intelligence/agents/prompts/posting_second_pass.v2.md:1-2`). Test: `tests/agents/test_prompt_fences.py`. Workflow v1 prompts keep JSON `text` fields (out of scope per the ADR).

## ADR-0133: data-class guard, OFF by default

- `config/models.yaml` `data_class_guard: "off"` (`config/models.yaml:195`); every model lists `data_classes_allowed: [public_web]` (e.g. `:200`).
- Modes `off` / `warn` / `enforce`; an unknown model or absent list means public web only; a bare YAML `off` (false) is refused (`src/llm/data_class.py:35-37`, `:62-77`, `:88-95`).
- A run's class is `tenant` when `run_input.input_mode == "file"`, else `public_web` (`src/account_intelligence/workflow/deps.py:147-150`). `_guard_data_class` logs `model.data_class_violation` and raises `DataClassRefused` under `enforce` (`:152-176`).
- The ABM graph maps the refusal to `model_data_class_refused` (`src/agents/graph.py:284-292`).
- **Not covered:** Ask/chat does not go through `resolve_model` (ADR-0133). With `enforce` and today's lists every model call of a file run would be refused.

## Code vs ADR

- ADR-0119 describes the fallback for "Kimi or Grok"; the code falls back for any non-Anthropic chosen model, while the loop comment in `RunDepsModel.call` still says "Kimi K3" only.
- ADR-0133 says the graph maps `DataClassRefused` to a PARTIAL end; that mapping exists only in the ABM graph (`src/agents/graph.py:291`). The file graph's own `_model_failure` list has no `DataClassRefused` entry (`src/documents/graph.py:71-90`), and file runs are exactly the runs classed `tenant`; under Workflow v1 or the file graph the refusal would reach the runner's `_stop_unexpected` (reason = error class).
- `config/workflow_v1.yaml:34` still has `judge_profile: "TODO, needs product owner; must resolve to a model distinct from the generator"` although G12 is now enforced in code.
