# 05 Spend and caps

Verified against the code on 2026-11-03 (working tree of `feat/final-product`, uncommitted changes included).

See also: [index](README.md) · [01 architecture](01-architecture-and-planes.md) · [02 request path](02-request-path-and-auth.md) · [03 run lifecycle](03-research-run-lifecycle.md) · [04 model routing](04-model-routing.md) · [06 data and isolation](06-data-and-isolation.md) · [07 documents](07-documents-and-sandbox.md) · [08 exports and gate](08-exports-and-gate.md) · [09 ops](09-ops.md) · [10 known gaps](10-known-gaps.md)

## The two switches: RESEARCH_CAPS and TOKEN_GUARD (ADR-0106)

- `research_uncapped()` is True when `RESEARCH_CAPS=off` (any environment) or `DEV_UNCAPPED=1` with `APP_ENV=dev` (`src/account_intelligence/dev_limits.py:37-47`). Unset or `on` means every cap applies.
- `token_guard_forced()` is True for `TOKEN_GUARD=on`: it keeps the token guard while the caps are off (`src/account_intelligence/dev_limits.py:50-55`).
- What `RESEARCH_CAPS=off` lifts, by enforcement site, is the one list `LIFTED_CAPS` (`src/account_intelligence/dev_limits.py:58-71`): run dollar cap, repeat-call limit, token guard (unless forced), per-agent budgets (`src/agents/contracts.py:102`), max slices (`src/account_intelligence/app/container.py:434`), the run's credit budget and call cap (`src/account_intelligence/runs/lifecycle.py:265-268`), credit pool (`src/account_intelligence/replica/credits.py:196`, `:235`, `:316`), researches per user (`src/account_intelligence/replica/limits.py:98`), plan limits (`src/metering/limits.py:118`), provider limits and read limits.
- **Cloud default is on.** `infra/stack` declares both variables with default `"on"` and validation `on|off` (`infra/stack/main.tf:88-104`) and passes them to every service and job via `common_env` (`infra/stack/main.tf:318-320`). Staging defaults both to `"on"` (`infra/envs/staging/main.tf:69-78`); prod passes neither and so inherits the stack default (`infra/envs/prod/main.tf` has no `research_caps`/`token_guard`). Guard test: `tests/unit/test_caps_default_on.py:64` (`test_caps_default_on_in_cloud_profiles`).
- A staging/production process started with caps off logs `research.caps_off` (WARNING, `caps_off` = the lifted names) from `app.container.warn_if_uncapped`, called in `build_container` (`src/account_intelligence/app/container.py:71-87`, `:465`).
- Whether live staging has been re-applied with `on` cannot be read from the repo: ADR-0106 says the staging plan carries unrelated drift and must be reviewed before apply.

## The run dollar cap

- `platform_defaults.run_dollar_cap_usd`, default $25.00 (`migrations/0067_resumable_research.sql:17`; code fallback `DEFAULTS` at `src/account_intelligence/connectors/spend_guard.py:97`). Editable on the operator Defaults page, 0.1 to 10,000 (`src/account_intelligence/api/routes/admin_console.py:850`).
- Spend counted = ledgered provider spend (`account_runs.spent_usd`) + priced model replies served by the model (`model_reply_log.cost_usd`) (`src/account_intelligence/connectors/spend_guard.py:117-129`).
- `check_cap` raises `DollarCapReached` when spent + this call's estimate passes the cap, and moves the heartbeat; with caps off only the heartbeat moves (`src/account_intelligence/connectors/spend_guard.py:132-145`).
- Called before every paid provider call inside the ledger claim (`src/account_intelligence/connectors/ledger.py:428-431`) and before every paid model call in `RunDeps._spend_check` (`src/account_intelligence/workflow/deps.py:360-371`).
- Separate from it: the per-run **budget** (`RUN_BUDGET_USD` or `max_budget_usd: 12.5` in `config/workflow_v1.yaml:25`; container `_limits`, `src/account_intelligence/app/container.py:413-421`), capped by the run's credits in dollars at creation (`src/account_intelligence/runs/lifecycle.py:258-264`). The budget pauses for approval; the dollar cap stops.
- **Repeat-call limit**: the same provider + operation + normalised first-page query may be paid at most `repeat_call_limit` (default 3) times; one more raises `RepeatedCallStopped` (`src/account_intelligence/connectors/spend_guard.py:9-14`, `:195-227`; called from the ledger at `src/account_intelligence/connectors/ledger.py:432-440`).
- Both stops end the run PARTIAL with the reason (`src/account_intelligence/connectors/spend_guard.py:29-30`); client text in `src/account_intelligence/replica/workspace.py:548-549`.

## The 3x token guard

- Threshold = `token_guard_ok` (tokens the user already accepted) + `token_guard_multiple` x `expected_tokens` (`src/account_intelligence/connectors/spend_guard.py:169-182`). The multiple is 3 (`config/spend.yaml:109`, default also 3 in `src/spend/config.py:176`, `:215-217`).
- Tokens used = the larger of `llm_calls` and `model_reply_log` (served by the model) input + output (`RUN_TOKENS`, `src/account_intelligence/connectors/spend_guard.py:145-150`, `:164-166`).
- Skipped when caps are off unless `TOKEN_GUARD=on` (`src/account_intelligence/connectors/spend_guard.py:174-175`); skipped for a run with no estimate (`:177-178`).
- Checked first in `RunDeps._spend_check`, before the dollar cap (`src/account_intelligence/workflow/deps.py:370-371`).
- ABM graph: a node that hits `TokenGuardReached` stops starting new calls, lets in-flight ones finish (`src/agents/graph.py:769-805`), then `token_gate` interrupts (`awaiting: cap, reason: tokens`); continue calls `accept_tokens` (records current tokens as accepted, so the next pause is another 3x on top) and re-runs the node; decline ends `token_guard_stopped` (`src/agents/graph.py:884-915`, `src/agents/production/credit.py:146-151`, `src/account_intelligence/connectors/spend_guard.py:185-192`).
- **Only job-started runs carry an estimate**: `expected_tokens` is written only by `GraphEngine` (`src/account_intelligence/replica/jobs.py:204-208`). A `POST /v1/runs` run has none and is never paused by the guard.

## Entity lock (ABM graph only)

- `entity_lock` node runs right after `setup`, before any pull (`src/agents/graph.py:1542-1543`, `:1574-1577`). It locks the account from its own home page and one brand search, records allowed hosts and look-alikes, and stores the lock; a build run reuses its parent's (`src/agents/graph.py:474-502`, pure logic in `src/agents/entity.py:1-11`).
- A home page that was read and names another company ends the run `entity_mismatch` (`src/agents/graph.py:499-501`), which is a pre-spend failure: FAILED, nothing charged (`PRE_SPEND_FAILURES` in `src/agents/production/wiring.py`).
- Later, `interpret` drops public records that fail `accept(lock, url, text)` as `off_target` (`src/agents/graph.py:726-736`, `src/agents/entity.py:243-265`).
- Workflow v1 and the file graph have no entity lock (no reference in `src/account_intelligence/workflow/` or `src/documents/graph.py`, checked by grep).

## Replay keys and REPLAY_KEY_VERSION (ADR-0129)

- Two kinds of key decide whether a resumed run reuses paid work: the ledger `CallKey.args_hash` over provider, operation, args (`src/account_intelligence/connectors/ledger.py:78`) and the model `reply_key` over task kind, tier (with `~effort`), token limit, system prompt, run-id-normalised user text (`src/llm/reply_cache.py:38-51`; built in `src/account_intelligence/workflow/deps.py:315-317`).
- The version lives in one place: `replay_key_version: 2` in `config/research_cache.yaml:42` (history at `:33-41`), read by `connectors.cache.replay_key_version()` (`src/account_intelligence/connectors/cache.py:82-84`).
- `create_run` writes `run_input.replay_key_version` on every run (`src/account_intelligence/runs/lifecycle.py:224-226`); a run without it counts as 1 (`src/account_intelligence/replica/workspace.py:863`).
- Golden file `tests/golden/replay_keys.json`: 175 call keys, 51 reply keys, version 2 (counted from the file). `tests/system/test_replay_keys.py:131` (`test_replay_keys_stable`) fails when keys change; `REPLAY_KEYS_REGEN=1` rewrites it (`tests/system/test_replay_keys.py:18`, `:134`). `abm_distill` and `abm_sense_check` are excluded because their requests list facts by database id (`tests/system/test_replay_keys.py:50`).
- Resume warning: `replay_extra_usd` is None when the run's version is current, else everything the source run paid, rounded up to the cent, as an upper bound (`src/account_intelligence/replica/workspace.py:911-924`); the account page's `ResumeResearch` shows "This resume may cost up to $X more" (`web/components/accounts/ResumeResearch.tsx:109`).
- Rule: when `test_replay_keys_stable` fails, bump `replay_key_version`, regenerate, and record the change (`config/research_cache.yaml:37-38`).

## Code vs ADR

- ADR-0106 names `dev_limits.warn_if_uncapped`; the function is `app.container.warn_if_uncapped` (`src/account_intelligence/app/container.py:71`), kept out of `dev_limits` by the "no `obs` in pure packages" contract (`pyproject.toml:363-369`).
- `RunDeps._spend_check`'s docstring says the token guard "applies with the caps off too"; `check_tokens` returns early with caps off unless `TOKEN_GUARD=on` (`src/account_intelligence/connectors/spend_guard.py:174-175`). The cloud sets `TOKEN_GUARD=on`, so the docstring holds there only.
