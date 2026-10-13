# Research cost audit, 2026-10-13

Scope: every research run started on 2026-10-11 and 2026-10-12 on the local stack, the worker
logs of the same period, the LangGraph checkpoints of the failed runs, and the paid-call ledger.

## What the ledger shows

| Measure | Value | Base |
| --- | --- | --- |
| Runs started | 27 | account_runs, 2026-10-11 and 2026-10-12 |
| Runs that ended COMPLETED | 0 | same |
| Runs that ended FAILED | 16 | same |
| Provider calls paid | 1902 | tool_calls, same period |
| Distinct calls (by arguments hash) | 612 | same |
| Provider dollars charged | 221.64 | tool_calls.cost_usd, same period |
| Of which page scrapes | 185.25 | 741 scrape_job_page calls at 0.25 |
| Model dollars | not recorded | the legacy model path wrote no usage anywhere |

Two in three paid provider calls repeated a call an earlier run of the same tenant had already
paid for. Replay existed only inside one run (pull once), so every retry of the same account
started from zero. The four largest runs each paid 50 to 55 dollars for their pulls and then
failed in the interpretation step, so nothing of that money produced a deliverable.

## Why the runs failed (from the checkpoints)

| Run | Node | Cause | State on HEAD before this work | After this work |
| --- | --- | --- | --- | --- |
| efe58555 | interpret | model provider 400, credit balance too low | run FAILED, 54.66 USD of pulls lost | solved: typed `ProviderCreditsExhausted`, run ends PARTIAL `model_credits_exhausted`, pulls kept and reusable from the cache; buying credits is the owner's step |
| 72c3fd2c | interpret | judge answered no JSON: max_tokens 1000 spent on thinking | fixed in class, limit still 1000 | solved: judge limit 4000, effort low, cached system block |
| d96058d4 | interpret | prompt of 1.13 million tokens | batching added in 70256ec; failure still fatal | solved: an oversized group is dropped and counted (`drop_context_too_long`), the run continues |
| 047378ea | targeted_pulls | model connection dropped | retry mapping added in 70256ec | solved before this work (retry), kept |
| 2dab4361, 94129b78, 33b0788f | build | missing_narrative: every narrative sentence dropped | open | solved: grade guard matches labels only; distill re-asks once; a refused build ends PARTIAL |
| bc0b94b8 | build | unconfirmed_stated in the Summary | open: FAILED instead of PARTIAL | partly: the run now ends PARTIAL `build_failed:build_failed` with the pulls kept; the wording rule itself is unchanged |
| cbd229bd | targeted_pulls | proof is cumulative | fixed in 70256ec | solved before this work, kept |
| 8c49fde6 | pull | no provider slot within 30 s | fixed in 70256ec | solved before this work, kept |
| 326414b1 | pull | identical call still in flight after a worker restart | open | solved: a started attempt of the same worker older than 900 s is re-issued (`in_flight_stale_s`) |
| d2c02ec0 | setup | invalid_setup | fixed in 70256ec (pursuing client optional) | solved before this work, kept |

The narrative drops came from one guard: any sentence containing the word "confirmed" (or
"probable", or "lead") was treated as a restated grade and removed, so narratives such as "the
company confirmed a new office" vanished whole and the Summary build failed closed.

## What changed

1. Research cache (`config/research_cache.yaml`, `connectors/cache.py`, ledger): a finished call
   of an earlier run of the same tenant with the same arguments and a payload younger than the
   operation's TTL is adopted by the new run at cost 0 (`cost_source = cache`). Tenant scope is
   row level security; a run with `run_input {"fresh": true}` pays for everything. Redis
   (`REDIS_URL`) indexes lookups; Postgres rows are the record.
2. Model reply log and reuse (`llm/reply_cache.py`, migration 0049 `model_reply_log`): every
   model call is logged with how it was served, tokens and priced cost; a byte-identical request
   (run id normalised out) is answered from the log with the same text and zero usage.
3. Routing (`config/models.yaml`): the `cost_saving` arm runs extraction and classification on
   Sonnet 5.5, planning and verification on Opus 5.5, synthesis and narrative on Fable 5.1;
   every task sends an effort level and its system prompt as a cached block. `automatic`
   restores the previous routing.
4. Token limits (`config/model_routing.yaml`): no task kind under 2000 tokens; the judge and
   setup had 1000 and 400, spent on thinking before any answer.
5. Failure handling: a refused build, a provider out of credits, a missing model key and a
   model that refuses now end the run PARTIAL with the reason and keep the pulls; an
   extraction group too large for the model is dropped and counted; a narrative the guards
   dropped whole is asked for once more with the reasons; the grade guard matches grade labels
   only; an in-flight call older than 15 minutes by the same worker is re-issued instead of
   failing the run.

## Still open

* Migration 0048 (not part of this work) has two SECURITY DEFINER functions without the
  `-- reviewed:` comment the migration linter requires.
* The pre-existing `tests/unit/test_env_registry.py` failures (unregistered dev and web
  variables) were fixed here by registering the variables; the web screenshot variables are
  registered under the web component.
* `config/model_routing.yaml` candidates still name an old fast model id and no prices; the
  router (`MODEL_ROUTER=on`) stays off until a benchmark exists (D-51).
* Cache TTLs are proposals; the product owner sets shelf lives.
* `scripts/token_savings.py` reports the savings from the ledger and the reply log.
