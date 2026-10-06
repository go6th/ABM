# Model routing guide (D-48, D-51)

* **Call a model** only through `RunDeps.model(task_kind, system, user)` with a task kind registered
  in `config/model_routing.yaml` and `src/llm/tasks.py` (a static test scans every call site).
* **Task kinds and tiers.** Extraction and classification: `extractor`; planning, synthesis,
  hypotheses, narrative, distill: `planner`; support judge, source re-read, narrative reread,
  posting second pass: `judge`. Knobs (max tokens; temperature null, see C21) are per task kind.
* **Ladder and fallback.** Selection only: the node RetryPolicy re-executes, the router picks the
  candidate for the next attempt from the call's failed attempts and the shared health. A
  non-primary candidate is `degraded` in `llm_calls`.
* **Records.** Every call is an `llm_calls` row (tokens, cache tokens, cost or null, alias,
  routing version, prompt hash); failed attempts are `llm_call_attempts`. A resumed run replays.
* **Changing routing.** Run the benchmark (`python -m evalgate.cli bench`), record the new config
  hash in `DECISIONS.md` with the report id and a sign-off, pass the eval gate. The router stays off
  (`MODEL_ROUTER` unset) until a benchmark-backed config exists.
* **Benchmark template.** `benchmark-template.md` (Blocked: needs analyst-labelled golden accounts,
  real model access and credits).
