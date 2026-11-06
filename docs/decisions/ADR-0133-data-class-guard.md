# ADR-0133: rule R8 rebuilt as a config-driven data-class guard on the live model path

- Status: Accepted (code); guard OFF until the owner turns it on
- Date: 2026-11-03
- Deciders: owner (fix prompt E, extra task)

## Context

The deleted model Router (ADR-0113 removed `config/model_routing.yaml`) had rule R8: tenant or
private data classes go only to models with zero data retention. It never ran on the live path;
its config listed `data_classes: [public_web]` with zero retention `TODO [VERIFY]`.

## Decision

- `config/models.yaml` gains a top-level `data_class_guard: "off" | "warn" | "enforce"` (default
  `"off"`, quoted: a bare `off` is YAML false and is refused) and, per model,
  `data_classes_allowed` (every model today: `[public_web]`, zero retention not verified; absent
  means `[public_web]`). Classes: `public_web`, `tenant`, `private` (`llm.data_class`).
- The check runs in `RunDeps.resolve_model` (the one live model path) after the model is chosen
  and **before** the in-run replay lookup, the cross-run reply cache, the spend check and any paid
  call. `RunDeps.model(..., data_class=None)` takes an explicit class; by default a run's class is
  `tenant` when its input is the client's uploaded files (`run_input.input_mode == "file"`) and
  `public_web` otherwise.
  - `off`: nothing happens (behaviour unchanged);
  - `warn`: logs `model.data_class_violation` (fields `task_kind`, `model`, `reason` = the data
    class, `outcome` = `warned`; all in `config/telemetry/logs.yaml`) and makes the call;
  - `enforce`: logs it with `outcome = refused` and raises `llm.data_class.DataClassRefused`
    (an `LlmError`). The research graph maps it in `MODEL_FAILURES` to
    `model_data_class_refused`: the node ends the run PARTIAL with that reason (like
    `ProviderCreditsExhausted` or `ContentRefused`), with client text in
    `replica.workspace.STOP_TEXT`.
- Tests: `tests/llm/test_data_class.py` (shipped config off and complete, mode checks, file run =
  tenant, off changes nothing, warn logs and calls, enforce refuses with no call made, the graph's
  failure code).

## Code disagreements with the prompt

- `resolve_model` did not receive a `data_class` argument (no `data_class` anywhere in `src/`); it
  was added (keyword, optional).
- The Ask/chat path (`chat/service.py`, `api/routes/ask.py`) does not go through
  `RunDeps.resolve_model` and passes no data class; it is **not covered** by this guard. Ask
  answers from the client's workspace (tenant data) on the model the user picks; covering it means
  adding the same check before its provider call.

## Turning it on

Verify each provider's zero-retention terms, list `tenant` (and `private`) on those models, then
set `warn` for a release (watch `model.data_class_violation`), then `enforce`. With `enforce`
and today's lists, every model call of a file run is refused.
