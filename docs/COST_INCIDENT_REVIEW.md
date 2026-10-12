# Cloud API cost incident: review and recommendations

Date: 2026-10-12. Scope: why a cloud run of the ABM pipeline billed about $166 of Anthropic API
credit (15 auto-reload invoices of $10.02 to $12.25) in a couple of hours, what to change, and how
to make a full run work locally in 30 to 45 minutes. Read-only review; no code was changed.

## 1. What went wrong (root cause)

The most likely explanation is one or two runs stuck in a re-pay loop, not many normal runs.

1. **The cloud deploy ran the dev container with a live model key.**
   `scripts/deploy_to_cloud_run.sh` passes `ANTHROPIC_API_KEY` but never sets `APP_ENV` (defaults
   to `dev`), `PAYLOAD_DIR` or `PAYLOAD_BUCKET`. The payload store is therefore in memory
   (`src/account_intelligence/app/container.py:314-316`).
2. **Runs execute in 240 s slices, up to 60 of them** (`config/dev.yaml`,
   `src/account_intelligence/workflow/runner.py:287`). A slice that times out throws away every
   model call still in flight; the next slice asks again and pays again. The code says so at
   `container.py:286-287`.
3. **The "don't pay twice" replay does not protect the cloud run.** Finished replies are replayed
   from `model_replies` payloads (`workflow/deps.py:149-167`), but:
   - the store is in memory, so every new or restarted Cloud Run instance loses it;
   - `source_reread` is `cacheable: false`, so it is never replayed. It runs on Fable 5.1 ($10/$50
     per Mtok), serially, one call per Confirmed claim, up to 500 per run, each with up to 20,000
     chars of source (`verify/stage.py:50-75`, `config/verify.yaml`).
4. **Single calls can outlive a slice.** ABM extraction calls run on Opus 5.5 with
   `max_tokens: 16000`, eight at a time (`agents/graph.py:92`). A 16k-token reply can take longer
   than 240 s, so the call is cut off and re-sent every slice.

A clean run costs roughly $10 to $20. A run caught in steps 2 to 4 for one to four hours reaches
$100 to $250, which matches the invoices.

## 2. Why nothing stopped it (no LLM spend cap exists on the live path)

| Stated limit | Reality |
|---|---|
| `RUN_BUDGET_USD=5`, `RUN_TOOL_CALL_CAP=60` | Cover data-provider calls only. `src/spend/proposal.py:73`: "Model (LLM) spend is reported but not counted against this cap". It is not even reported (`llm_micros` is never passed). |
| `config/models.yaml` `spend_limit_usd: 5.00`, `turn_limit` per phase | `ModelRouter.check_limits` / `calculate_cost` / `log_call_usage` (`model_routing.py:189-316`) have no callers. Dead code. |
| `config/model_routing.yaml` budgets | The router only runs with `MODEL_ROUTER=on`, and refuses to start because `benchmark_report` is null. Even if on: every price is null (cost unknown, not counted), `token_caps` are null (check returns "unset", call proceeds). |
| Agent token budgets (`config/agents.yaml`) | Charged after the call, shared across 8 concurrent calls, skip schema-failed calls, and smaller than one call's `max_tokens`. `DEV_UNCAPPED=1` (set in `compose.live.yml`) raises them to 1,000,000. |
| Chat | Capped at $1000 per thread, 1000 turns, unlimited threads. The $2 chat limit in `models.yaml` is unused. |
| Tenant concurrency 5 | Not read by any code. Parallel runs multiply spend. |

The live call itself, `AnthropicLlm.complete` (`src/account_intelligence/platform/llm.py:75-100`),
is a bare `messages.create`: no budget check, no cost record, no prompt caching. Nothing in the
app logs per-run LLM cost; the Anthropic console is the only record.

## 3. Other cost multipliers

- **Model choice.** All extraction, classification, planning, sense-check and distill run on Opus
  5.5 ($4/$20). All judging (`support_judge` per claim per evidence sentence,
  `second_pass_posting`, `source_reread`) runs on Fable 5.1 ($10/$50). Haiku is configured in
  `model_routing.yaml` but never used, because that file is not the live router.
- **No prompt caching anywhere** (legacy path, router path when off, chat). System prompts are
  stable, so caching would hit.
- **Large inputs and outputs.** ABM extraction sends up to 120k chars (about 30k tokens) with 16k
  max output; `extract_facts` sends about 40k tokens with only 4k max output, so truncation then a
  JSON re-ask with the identical input is likely. Re-asks can pay 2x to 4x per call
  (`agents/production/wiring.py:79`, `workflow/stages/research.py:49`, `grading/judge.py:88`).
- **Node retries** (`config/providers.yaml`, 3 attempts on 429/5xx/timeout) re-run the whole node,
  including non-replayable calls.
- **`DEV_UNCAPPED=1`** lifts pagination, job, people and slice caps, so more payloads feed more
  expensive extraction calls; deep runs do up to 5 sense-check rounds.
- **Security:** the worker is deployed `--allow-unauthenticated` with
  `ALLOW_UNAUTHENTICATED_TASKS=1`; anyone with a run id can POST `/tasks/run-slice` and trigger
  paid calls.

## 4. Recommendations

### Do today (no code)

1. In the Anthropic console: turn **auto-reload off**, set a **monthly workspace spend limit**, and
   use a separate API key for this app so its usage is isolated.
2. Take the Cloud Run worker down or make it authenticated (remove `--allow-unauthenticated` and
   `ALLOW_UNAUTHENTICATED_TASKS=1`). Rotate the API key that was deployed.
3. Do not run the pipeline in the cloud again until items 4 to 8 are in.

### Code changes, in priority order

4. **One hard per-run LLM USD cap on the live path.** In `AnthropicLlm.complete` (or `RunDeps`),
   reserve the worst case (input estimate plus `max_tokens` at the model's price) before each call,
   record actual usage including cache tokens after it, and stop the run when the cap is reached.
   Add a global daily cap too. Log `run_id, task_kind, model, tokens, cost` per call.
5. **Stop re-paying.** Make replies durable (`PAYLOAD_DIR` locally, `PAYLOAD_BUCKET` in cloud), make
   `source_reread` replayable or batch it, and size slices so one call always finishes inside one
   (for example, `slice_seconds` of 900 to 1200 with `max_slices` of 6 to 8).
6. **Right-size models.** Extraction, classification and `classify_person` on Haiku 4.5 or Sonnet;
   keep Opus for planning, sense-check and distill; drop Fable from per-claim judging (use Sonnet or
   Opus, and judge in batches of claims rather than one call per claim per sentence). Use one routing
   file, not three (`models.yaml`, `model_routing.yaml`, `chat_models.yaml`), and fill in real prices.
7. **Enable prompt caching** (`cache_control` on the stable system prompt and schema).
8. **Cut `max_tokens`** to what the output needs (extraction 4000 to 6000, not 16000) and raise
   `extract_facts` so it stops truncating and re-asking; cap re-asks at one per call.
9. **Chat:** per-thread cap of a few dollars, a daily per-tenant cap, and Sonnet rather than Fable.
10. Enforce run concurrency (one or two runs at a time per tenant).

Expected effect: a full run at about $2 to $5, with a hard stop that cannot be exceeded.

## 5. Running the whole program locally in 30 to 45 minutes

The backend itself works locally. In the review, Postgres 16 with pgvector 0.8 and the native API,
worker and fake IdP came up, all 48 migrations applied in about 2 s, and a run was accepted. The
local errors come from setup and documentation problems:

| Problem | Fix |
|---|---|
| pgvector older than 0.7 (Ubuntu 0.6, older Homebrew/Postgres.app): migration 0002 fails with `type "halfvec" does not exist` | Use the `pgvector/pgvector:pg16` image or pgvector 0.7 or later |
| Python 3.13 or 3.14 on the machine; project needs 3.11 or 3.12 (`pyproject.toml`) | `uv sync` (uv fetches 3.12); add a `.python-version` file |
| `fake-idp` uses the worker's image but has no build section | Build the worker first or give fake-idp its own build |
| gcs and tasks emulators, and Redis, are unused by the app (`:latest` tags, may lack arm64 images) | Drop them from the local stack |
| Docs: `cd "ABM Generator"`, `/v1/health` (404; real ones are `/healthz`, `/readyz`), JWKS path, "migrations run on startup", the `doctor@synthetic.local` password login that does not exist | Correct README.md and DEPLOYMENT.md |
| Runs stall at AWAITING_APPROVAL: no local admin can sign in without MFA | Only `compose.live.yml` auto-approves; add a local approve script |
| The documented admin token for `doctor` returns 401 (membership is `viewer`) | Seed a seller/admin membership and fund the wallet |
| Compose v2.24 or later required (`!override`, `env_file` with `required`) | Update Docker Desktop |
| Repo is not green: `make lint` 70 errors, mypy 2 errors, 13 failing tests, coverage 92.9% vs 94% gate | Fix before the next release |

Recommended local profile for a 30 to 45 minute run: db, migrate, api, worker, fake-idp and web
only; `PAYLOAD_DIR` set to a real folder; slice 1200 s with about 3 slices; a per-run LLM cap of
about $5 (item 4); cheaper extraction models (item 6); `DEV_UNCAPPED` off, with pagination, jobs
and people caps kept. Send the exact local error text to confirm which row above it is.

## 6. Database recommendations

Keep PostgreSQL. The design needs it throughout (row-level security, pgvector `halfvec`, hash
partitions, the LangGraph Postgres checkpointer, triggers); SQLite is not an option without a
rewrite.

1. **Local:** one Postgres 16 container from `pgvector/pgvector:pg16` (pgvector 0.7 or later), a
   named volume, migrations run by the one-shot `migrate` service. `CHECKPOINT_DATABASE_URL` must be
   plain `postgresql://` (no `+asyncpg`) and connect directly, with no pooler.
2. **Record model spend in the database.** Write every model call to the existing `llm_calls`
   table (today only the unused router writes it) and add LLM cost to `account_runs.spent_usd`, so
   the run budget and the UI see it.
3. **Store model replies durably** (payload directory or bucket, keyed by run), so retries and
   restarts replay instead of re-paying.
4. **Tests:** the Makefile points the checkpointer at the postgres superuser, which bypasses RLS;
   point it at `app_rw`.
5. **Before production:** run the live catalog check of roles, policies and grants on every tenant
   table (BACKEND_AUDIT.md), and close the open items in docs/open-items.md: IAM auth and private IP
   (V-SQL-1), checkpointer over the Cloud SQL socket (V-SQL-2), concurrency on Cloud SQL (V-SPEND-2),
   migration 0017 with a non-superuser migrator (V-GOV-1), Send-resume under the Postgres saver
   (V-LG-1), backups/PITR/failover (V-P4-SQL-1/2). Update BACKEND_AUDIT.md (it says migrations
   through 0047; there are 48) and fix the 13 `lint_migrations.py` findings.
6. **Pool sizes:** keep 10 + 5 overflow per instance, and size Cloud SQL connections for
   (instances x 15) plus migrations.
