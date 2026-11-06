# 4. Config map: which file controls what

*Part of the [project guide](README.md).*

The folder `config/` holds about 70 YAML files (71 at the time of writing). They are **rules the code reads as data**, so a
rule can change without editing Python. Every file starts with a comment saying where the rule
came from and whether it is a sourced rule or a *proposal* waiting for the owner. At start-up
`account_intelligence/config_files.py` loads and checks them and takes a hash of each, so a run
records exactly which rules it used. A bad file stops the program from starting (on purpose).

Read `config/README.md` for the owner's own notes. This page groups the files by purpose and
names the code that reads each one.

## A. The ones an operator tunes on purpose

| File | What it controls | Read by |
|---|---|---|
| `workflow_v1.yaml` | the 13 research stages and the time limits: `slice_seconds` **1650**, `max_slices` 16, `max_no_progress_slices` **3**, `max_calls` 60, approval timeout 24 h | `workflow/engine.py`, `app/container.py` |
| `models.yaml` | which AI models are allowed, their prices, and the routing "arms" (which model does which kind of work) | `platform/llm.py`, `agents/graph.py`, `llm/*` |
| `model_routing.yaml` | task kind → tier → ordered ladder of model candidates. The only file that holds model ids. A change needs a recorded decision. | `llm/config.py`, `llm/tasks.py` |
| `providers.yaml` | the internal data-provider list (the only place vendor names may live): addresses, key names, timeouts, retries, circuit breaker, price per call, ledger lease (1800 s; renewed every 20 s while a slice runs; taken over after 90 s of silence) | `connectors/*`, `spend/*` |
| `credits.yaml` | 1 credit = $0.05; the credit estimate per kind of job (jobs 20, people 60, full 250, topic 40). With the caps on, an estimate built from the account's history never falls below **half** of the rate, and a resume reserves for the steps it has left (not below that half) | `replica/credits.py` (`estimate`, `estimate_resume`) |
| `spend.yaml` | the per-run spend cap rules and the token-guard multiple (3×) | `spend/*`, `connectors/spend_guard.py` |
| `rate_limits.yaml` | per-user limits on money/compute actions | `identity/rate_limit.py` |
| `usage_estimate.yaml` | the token and dollar estimate shown before the user presses Proceed (defaults until the workspace has finished runs of its own; full research 2.8M tokens, $12.50 since 2026-10-30) | `replica/usage_estimate.py` |
| `research_cache.yaml` | how long earlier answers may be reused (web 7 days, news/jobs 3, tech 14, org 30, people 14). **The main money saver**, and what makes a Resume free. | `connectors/cache.py`, `llm/reply_cache.py` |
| `agents.yaml` / `lessons.yaml` | per-step model tier, budgets, retries; lessons turned into code with a guarding test | `agents/config.py`, `agents/lessons.py` |

**The Cloud Tasks queues are not YAML either** (`infra/stack/main.tf`, `queues`). The `runs` queue
(research slices) allows **8 attempts**, 10 s backoff doubling four times, 300 s at most: retries at
about +10, +30, +70, +150, +310, +610, +910 s after a failed delivery. The lease takeover of a killed
worker (`providers.yaml` `ledger.lease_stale_after_s` 90 s) happens at the +150 s retry;
`tests/unit/test_slice_config_consistency.py` computes this schedule from the four numbers in the
Terraform file and fails if the takeover no longer fits.

**Caps are not a YAML file.** They are the environment variable `RESEARCH_CAPS`. `off` lifts
the credit, budget, tool-call and slice caps in *any* environment
(`account_intelligence/dev_limits.py`, function `research_uncapped()`). Terraform sets it
(`infra/envs/staging/main.tf`, default `off` since 2026-10-21). With caps off the connector layer
also raises every timeout, wait and retry (`connectors/settings.py`, `uncapped_config`). Two
things stay on regardless: the **no-progress cut-off** and the **token guard** (if `TOKEN_GUARD=on`).

## B. Grading and evidence

| File | Controls |
|---|---|
| `grading.yaml` | the scoring maths: source-tier weights (T1 0.90 … T4 0.40), freshness half-lives, grades A–D, eval gates |
| `grading_policy.yaml` | the order of rules that turn a score into Confirmed/Probable/Hypothesis/Lead; what may be stated to a prospect |
| `source_registry.yaml` | which web domains are first-hand (T1) and which are aggregators |
| `routing.yaml` | the tool categories used to decide whether a Lead is verified (3 queries across 2 categories) |
| `checklist.yaml` | the 33 coverage rows (5 in pass 1, 28 in pass 2) |
| `tracker_v1.yaml` | when a run may be called Completed; reason codes; consistency rules |
| `verify.yaml`, `evals.yaml`, `regression.yaml` | the blind verifier, the evaluation thresholds, the regression paths |
| `scoring.yaml`, `shelf_life.yaml`, `refresh.yaml` | signal significance, how long a fact stays fresh, the scheduled refresh |
| `platform_dictionary.yaml`, `platform_tiers.yaml` | the platform ranking sheet and tier counts used by Ask |

## C. The research plan (what a run does)

| File | Controls |
|---|---|
| `research_scopes.yaml` | which checklist rows each scope pulls: `full`, `jobs`, `people`, `topic` |
| `research_input_modes.yaml`, `research_files.yaml` | company mode vs file mode; uploaded-file limits |
| `parallel.yaml` | how many calls run side by side. `providers.ceilings` (floor 4, `p_search` 8, others 4) are the governor's slots per provider **and** the size of a research's own gate per provider (`crosscut/gates.py`, used by `agents/production/provider.py`, `jobs/adapters.py`, `people/adapters.py`): at most that many of one research's calls are at a provider at a time, the rest wait in the research with no time limit instead of in the governor's 30 s wait. The scrape provider's 4 is below every paid Firecrawl plan's concurrency (Hobby 5, Standard 25) and is the pull's speed limit: raise it with `rate_per_s` in `providers.yaml` once the plan is known (owner decision) |
| `lanes.yaml`, `job_lanes.yaml` | the platform "lanes" (Salesforce, ServiceNow, SAP…) and their word lists |
| `jobs_v1.yaml`, `posting_rubric.yaml` | the job-postings pipeline caps and posting classes |
| `people_rubric.yaml`, `people_scope.yaml`, `people_sources.yaml` | the people pipeline (scope: Director and above) |
| `rag.yaml` | retrieval thresholds for Ask |
| `skill/` | the owner's ABM research skill (v1.11): sources, pull plan, attributes, consent. It is injected into every research model call as system context (`agents/skill_context.py`). `skill/pull_plan.yaml` holds, per coverage row, its step, route, search queries (91 for the web rows), `own_site` (also search the account's own site and read what is found there live), **`follow`** (own-site links a row reads after its pages: words, `per_query` count, scope window) and **`also_on`** (the query also searched on a register or the newswires, restricted to named hosts, window-bounded; `agents/config.py` refuses a malformed rule at load) |

## D. Deliverables (the files the client receives)

`output_spec.yaml` (which files and the quality gate), `export_rules.yaml` (content rules),
`export_theme.yaml` (the one place a style value may live),
`client_output_denylist.txt` (words that may never reach a client file; the scanner is
`safety/client_output.py`).

## E. Security, privacy, tenancy

`auth.yaml`, `session.yaml`, `roles.yaml` (sign-in, sessions, roles admin/analyst/seller/viewer),
`support.yaml` (time-boxed staff access), `audit.yaml`, `ownership.yaml` (which module writes which
table; a test fails if a table is in neither list), `deletion.yaml` (every tenant table is erased,
blocked or kept, with its reason; a test fails on one in none), `retention.yaml`, `dsar.yaml`,
`minimisation_map.yaml`, `tenant_settings.yaml`.

## F. Platform, billing, product extras

`billing.yaml`, `plans.yaml`, `budgets.yaml`, `connectors.yaml` (the client's own mail/drive/CRM
connections), `crm.yaml`, `alert_rules.yaml` + `alerts_extra.yaml`, `pilot.yaml`, `views.yaml`,
`drift.yaml`, `section11_spec.yaml`, and the folders `telemetry/` (the logging and alert contract,
turned into Terraform by `scripts/gen_observability.py`) and `hermetic/` (fake model and fake
providers for tests and demos).

## G. Files with no code reader found

`claims.yaml`, `messaging_rules.yaml`, `pricing.yaml`, `secrets_rotation.yaml`,
`soc2_controls.yaml`, `risk_register.yaml`, `data_model_spec.yaml` are registers or documentation;
no product code reads them (tests may). Treat them as notes, not switches.
