# Prompt: build Model routing, Verifier agents and the Eval harness (tiered routing with fallback across providers, fresh-context verification that can only downgrade, golden-account evals that block a regressing merge), production grade

Paste everything below the line into Claude Code, opened in the project repo (built with the `account-intelligence-platform` skill).

---

Build three coupled AI work packages and prove them with a real harness. **Model routing:** extraction and classification go to a faster, cheaper model; synthesis, hypotheses and narrative go to a stronger one; on error the call **falls back across providers**; the split is decided from **Phase 0 benchmark data**, never from taste. **Verifier agents:** a separate agent with a **fresh context re-reads the source for every Confirmed claim, checks figures against their base, and rereads the narrative against the tables; disagreements downgrade the grade and are logged.** **Eval harness:** golden accounts scored on **coverage completeness, citation validity (the URL opens and the sentence is present), grade accuracy on an analyst-labelled sample, and unsupported-claim rate**; it runs in CI on every prompt, model or code change, and **a regression blocks the merge.** Write production-grade code. Never invent model names, prices, limits, labels, thresholds or process rules, and never let a model compute a metric, upgrade a grade, choose its own model or pass a gate.

## 0. Ground rules

1. Read `.claude/skills/account-intelligence-platform/SKILL.md` first and obey it (rules 3, 4, 5, 8, 10 and 12 bind here). Then read `references/04-run-lifecycle-and-agents.md` (model tiers, caching playbook, graph nodes, budgets), `references/06-evidence-and-grading.md`, `references/10-testing-evals-cicd-ops-cost.md` (eval gates, golden set, CI), `references/11-build-plan.md` (spike 3 and the evaluation harness rule), `references/12-open-decisions-and-verification.md` (D-02, D-11, V1, V5), `references/13-starter-code-and-known-defects.md`, `assets/grading.yaml`, `assets/checklist.yaml`, `BUILD_LOG.md`, `docs/STATUS.md`, and the specs of earlier work packages in `docs/prompts/` (`docs/grading/`, `docs/workflow/`, `docs/providers/`, `docs/spend/`). If `docs/sources/` holds the PRD, cite 3.6 (success metrics) and 3.4 gate rule 5.
2. **What the sources say (the whole rule set; do not add to it as fact):**
   - Tiers are **config, never hard-coded model ids**: Planner (plan, distill, render), Extractor (baseline, quarantine extraction, normalisation helpers), Judge (verify, offline eval; **must differ from the generator**), Embedding, Rerank. **A fallback chain within a tier before degrading quality; a degraded run is marked.** Model ids change only through the evaluation gate and use dated identifiers where offered. Every id and price in the Architecture is **illustrative or `[VERIFY]` (V1)**. Start with the stronger model on distill and build; test a faster model on pulls; **measure gate-failure rate per step and model before committing** (D-02).
   - Fixed max tokens and temperature per node; the extractor has no tools and emits schema-validated JSON; broker 429 and 5xx and LLM overload belong to the node `RetryPolicy` (one retry owner); an LLM token cap per run per depth tier.
   - Prompt caching: prefix order, no tenant data in the cached prefix, deterministic bytes, record cache-read, cache-write and uncached tokens, alert when cache-read ratio is below 70% on cacheable calls.
   - Eval gates (**initial values, tune on data**): precision of grade A at least 0.95 and B at least 0.85; coverage of expected attributes at least 0.80; citation validity at least 0.97 (the cited span must support the claim); grade calibration error at most 0.05; injection and leakage suite 100%; cost per run within 10% of baseline; p95 duration within 15% of baseline. A candidate is blocked if any gate fails or a paired test shows a statistically significant drop on a primary metric. Golden set 50 accounts growing to 200, stratified by size, industry and region; **every label has a capture date and is re-verified quarterly**; the judge differs from the generator and is calibrated against human labels; candidate and baseline run on the same accounts with confidence intervals; an adversarial corpus of injection pages and poisoned documents; shadow-grade a small share of live runs; the harness is built alongside the graph.
   - PRD gate rule 5 (narrative reread against the tables) and success metrics 3.6 (coverage completeness, claim traceability, corrections per run).
3. **Fixed by the skill:** untrusted text is read only by the tool-less extractor; no fact without a source, lineage and a formula-computed grade; only Confirmed may be stated to a prospect; the product grade of a claim is computed by the grading engine (this package may only ask it to downgrade); the database enforces isolation; no client output names a provider; nothing is deleted or overwritten (a downgrade is a new superseding row).
4. No real model or provider calls, no real spend, no real credentials or tenant data while building. Scripted fakes, recorded fixtures and `synthetic` data only; every `live` path is skipped unless an explicit environment variable and a spend cap are set.
5. Never claim a gate passed that you did not run. Never weaken or delete a test to pass. Commit on branch `feat/ai-routing-verify-eval` in small Conventional Commits. Do not push.

## 1. Preflight (before writing code)

1. Print a table of what exists: `LlmPort` and its adapters and fake, tier config, `src/agents/prompts/` and their hashing, the grading engine (`record_graded_fact`, `apply_support_verdict`, `statement_policy`, downgrade and grade-event writers), `SupportJudgePort`, workflow stages `s10_distill_grade` and `s10b_judge_verify`, the evidence gate and quote check, the number registry, `deliverables` and report sections, the golden loader (P0-4) and fixtures, any benchmark harness (P0-2), recorded provider cassettes, `tests/evals/`, the pytest `eval` marker, `.github/workflows/`, the ledger and token budget code, `tenant_txn`, the isolation suite.
2. **Prerequisites.** Grading engine, workflow engine and connector cassettes must exist. If the grading engine's downgrade API or the workflow's judge stage is missing, **STOP and tell me which prompt to run first**; do not rebuild them. Missing pieces (`SourceTextPort`, `ClockPort`, `EvalStore`, `GoldenStore`, `ModelHealthStore`) are typed `Protocol`s with in-memory fakes in `tests/fakes/`; production adapters that do not exist raise `NotConfigured`, and the affected claim is **not** confirmed (never a fake pass).
3. **One implementation per rule.** Reuse the grading engine's tiers, `statement_policy`, normalisers and span checks, the workflow's quote check, the number registry, the golden loader, and the connectors' cassettes. This package routes, verifies and measures; it never re-grades, never re-normalises and never copies a rule.
4. Extend tables **expand-only**; never drop or alter an existing column (ask first if unavoidable).

## 2. Anti-hallucination protocol (build time)

1. **No invented models, prices, limits, labels or thresholds.** Candidate model ids, context windows, prices, rate limits, structured-output and caching support, and data-retention terms are configuration with `source` (a dated provider document or a file I supply), or `source: TODO, needs product owner`, or `status: proposal`. Unknown means `null`, surfaced as a warning, never defaulted. Golden labels come only from analyst-reviewed accounts; build the format, the loader and `synthetic` fixtures and list the exact files I must supply.
2. **Proposals of mine, not facts from the sources** (ADR, register entries at the next free numbers; earlier prompts used D-12 to D-47, so start at **D-48**; new conflicts at the next free `C` number):
   - **D-48 routing:** a task-kind to tier map in config; **fallback is selection, not retry** (the node `RetryPolicy` stays the only retry owner, the router picks the candidate for attempt n from a configured ladder and from candidate health); degradation is recorded; judge independence is by **model family**; candidates declare allowed data classes and only a verified zero-retention candidate may see tenant or private data (V5; default public-web-only).
   - **D-49 verification:** the verifier re-reads the source **blind** (it is not shown the claimed value), code compares its answer with the claim, authority is **downgrade only**, a Confirmed claim that cannot be verified is **capped** by the same policy as an unavailable judge (G12), narrative rereading yields verdicts only and **code** applies them. The existing cheap support judge (claim plus cited sentence) stays; the verifier adds the source-level re-read for Confirmed claims.
   - **D-50 eval modes and statistics:** `replay` (recorded model outputs and provider cassettes, deterministic, every PR), `live` (real models against cassette providers, whenever a prompt, model, routing or agent-path change is detected, under a hard budget), `nightly` (full golden set including holdout, live URL checks); paired comparisons with Wilson intervals and a paired bootstrap, **Holm correction** across primary metrics, fixed seeds, k repeats for model variance; the ARCH thresholds are the initial values; "the URL opens" is the stored fetch record in CI and a live check nightly.
   - **D-51 governance:** routing config changes only through a benchmark report, a human sign-off recorded in `docs/routing/DECISIONS.md`, and the eval gate.
3. **Verify every library and API fact** (provider SDK structured-output and caching parameters, error classes, token usage fields, the CI platform's required-check behaviour, statistics library functions) against installed packages and current docs; mark anything unverified `[VERIFY]` in `docs/open-items.md`. Do not state performance numbers; measure them.
4. No vendor or broker name in tenant-facing output, client files, logs at info level or config keys outside `config/model_routing.yaml`. Model providers are internal `provider_code`s and appear only in internal tables and the operator view.

## 3. Anti-hallucination protocol (run time)

### 3.1 RouteGuard
| # | Rule |
| --- | --- |
| R1 | **No model id outside config.** A static test fails on any model-id-shaped string in `src/`, prompts or tests. Config is validated and hashed at startup: every task kind has a tier, every tier has at least one candidate, every candidate declares provider, family, context window, data classes and price source. |
| R2 | **Task kind is mandatory.** Every call site passes a registered `task_kind` (extract facts, classify posting, classify person, plan gaps, synthesise hypotheses, write narrative, support judge, source re-read, narrative reread, and so on). A static scan of all `LlmPort` call sites fails on an unregistered or missing kind. Extraction and classification map to the extractor tier; synthesis, hypotheses, narrative and distill to the planner tier; verification to the judge tier (ARCH tier table and the roadmap). |
| R3 | **Output is validated in code.** Every response is validated against its JSON Schema regardless of provider-native structured modes; one bounded re-ask on a schema failure, then a typed error; nothing unvalidated reaches a repository. |
| R4 | **Fallback is selection.** The router picks the candidate for attempt n from the configured ladder and candidate health (shared circuit state); fallback applies to retryable exhaustion per the ladder, `model_unavailable` and `context_too_long` (to a larger-context candidate); **never** to a schema failure, an authentication error (page the operator) or a content outcome. No second retry loop exists. |
| R5 | **Degradation is visible.** A call served by a non-primary candidate or a fallback tier records `degraded` with a reason in its call record and the run state; the run summary counts them; evals compare degraded and clean runs. |
| R6 | **Judge independence.** The judge's model family must differ from the family that produced the claims in this run (recorded per batch). A violation raises `JudgeNotIndependent` and takes the unavailable-judge path (G12 caps); it is never waved through. |
| R7 | **Fixed knobs.** Temperature, max tokens and other sampling parameters come from config per task kind and cannot be overridden by input; prompts are versioned files and the run pins `routing_version` and prompt hashes. |
| R8 | **Data classes.** The router refuses to send tenant or private data to a candidate without a verified zero-retention flag and refuses to send anything to a candidate whose data class is not declared. |
| R9 | **Budget before the call.** The per-run, per-depth-tier token cap is enforced before the call; cost, tokens and cache tokens are recorded per call in the ledger; an unknown price is flagged, never zero; a resumed run replays stored responses by call key and does not pay twice. |
| R10 | **Cache hygiene.** Deterministic prefix bytes (tools sorted, canonical JSON, no timestamps or ids), no tenant data in the cached prefix, cache telemetry on every call; only candidates that declare caching support get cache controls. |
| R11 | **Routing never depends on model output.** A model cannot choose, change or veto a model, a tier, a prompt or a budget; untrusted text is read only by tool-less calls whose output fills typed fields. |
| R12 | **Governed changes.** A diff to `config/model_routing.yaml` fails CI unless `docs/routing/DECISIONS.md` references a benchmark report id and the eval gate has passed for that diff. |

### 3.2 VerifyGuard
| # | Rule |
| --- | --- |
| V1 | **Fresh context.** Each verification is a new request with no shared history, no generator reasoning, no tools, and the minimum input: for a claim, the stored source text and an attribute question; for the narrative, one sentence and its cited rows. |
| V2 | **Blind re-read.** The verifier is **not shown the claimed value**. It answers the attribute question from the source and returns a verbatim quote; code checks that the quote exists in the stored source text, normalises the answer with the same normaliser as facts, and compares it with the claim. Agreement confirms; anything else is a disagreement. |
| V3 | **Downgrade only.** The only outcomes are keep, downgrade, drop or flag. A verifier can never upgrade a grade, add or edit a fact, or rewrite narrative. A downgrade is a new superseding fact row plus a grade event (G15) carrying reason, claim id, verifier alias, prompt hash and span hashes. A hallucinating verifier can cost recall, never create a false Confirmed. |
| V4 | **Verified or capped.** A Confirmed claim must carry a `verified` result for this run and this source text hash; if the verifier is unavailable, over budget, not independent or the source is unreadable, the claim is capped by the G12 policy and is never delivered as Confirmed. |
| V5 | **Figures are checked in code.** Every figure is recomputed from rows and the number registry against its stated base. A model may only locate figure mentions in free text that patterns cannot find; comparison is code. A mismatch downgrades the backing row or flags the sentence; quoted source figures also pass the existing quote check. |
| V6 | **Narrative verdicts, code actions.** Per sentence: supported, overstated, unsupported or contradicted against its cited rows only. Code applies `statement_policy`: overstated forces the grade-appropriate phrasing by template; unsupported or contradicted removes the sentence from the build input; every action is logged; the model never writes replacement text. |
| V7 | **Injection safe.** Source text is untrusted: the verifier is tool-less, its output schema-validated, and nothing it says is executed or followed. |
| V8 | **Idempotent.** Results are keyed by claim id, source text hash, prompt hash and candidate class; a resumed run replays stored verdicts at no cost. |
| V9 | **Bounded cost.** Batch by source, spend within a configured share of the token cap, order High-row claims first; claims left unverified are capped (V4). |
| V10 | **Disagreement is data.** Every disagreement is an insert-only `verifier_findings` row and a run event, counted per model and prompt, shown to the review screen and fed to the eval harness; client output shows only the final grade. |

### 3.3 EvalGuard
| # | Rule |
| --- | --- |
| E1 | **Metrics are code.** Computed from stored rows and labels; a model never computes or reports a number. An LLM judge may only label support, and only once its agreement with human labels is calibrated above a configured floor; otherwise its output does not gate. |
| E2 | **Golden integrity.** Every label has a capture date, a source, an opaque analyst id and a split (dev or holdout); labels older than the configured age are excluded and counted; a **contamination check** fails if any label or golden source text appears in a prompt, example or fixture. |
| E3 | **Exact metric definitions, each with its base:** coverage completeness (expected attributes found over expected, and High tracker rows Done or None over High rows); citation validity (cited claims whose URL has a successful stored fetch record **and** whose sentence is present verbatim in the stored text, over cited claims; nightly adds a live open check); grade accuracy (per-grade precision and recall against analyst labels, confusion matrix, calibration error over fixed bins); unsupported-claim rate (unsupported over all claims); cost per run; p95 duration (live and nightly only; `not_applicable` in replay). |
| E4 | **Paired and corrected.** Candidate and baseline run on the same accounts and seeds; a regression is any absolute threshold failure or a statistically significant drop on a primary metric after Holm correction; intervals are reported; thresholds are config with `source: ARCH` and are initial values. |
| E5 | **Modes and missing results.** A required mode that did not run is `not_run` and **blocks the merge**; budget exceeded, infrastructure error and timeout also block; there is no override in code. |
| E6 | **Variance handled.** Fixed temperature and seeds; k repeats for model calls; per-metric variance reported; a metric that flips on identical input is flagged flaky and blocks until stable. |
| E7 | **Security evals are zero tolerance.** The injection corpus, leakage canaries and cross-tenant checks must pass 100%; one failure blocks. |
| E8 | **Immutable baselines.** The baseline is the last green `main` run, stored with code sha, prompt hashes, routing hash and golden version; it is never edited; a better baseline is a new row. |
| E9 | **No real client data and a hard budget.** Only synthetic and consented golden data (stored source snapshots); replay needs no network; live mode stops at its spend cap and fails closed. |
| E10 | **The harness tests itself.** Known-answer tests for every metric and statistic, an A/A suite that bounds the false-block rate, and seeded regressions that must block. |

## 4. Architecture to build

```
src/llm/
  config.py  models.py  errors.py  port.py        LlmRequest, LlmResponse, Usage, typed errors
  routing.py  health.py                           task kind to tier to candidate; ladder; shared circuit state
  schema.py   cache.py   budget.py   cost.py      validation and bounded re-ask; prefix and telemetry; token caps; prices
  record.py   prompts.py                          call records; versioned prompt loader and hashes
  adapters/   anthropic_api.py  vertex_ai.py  fake.py      (adapters verified against installed SDKs [VERIFY])
src/verify/
  claims.py   blind source re-read and comparison            (V1, V2, V4)
  figures.py  recomputation against base                     (V5)
  narrative.py  per-sentence verdicts and the code actions   (V6)
  policy.py   pure: verdict to action, downgrade-only        (V3)
  stage.py    workflow adapter extending s10b_judge_verify; ports.py
  prompts/  schemas/
src/evals/
  golden.py  runner.py  metrics.py  stats.py  gates.py  baseline.py  report.py
  calibrate.py  adversarial.py  contamination.py  bench.py  cli.py
.github/workflows/eval.yml                        required check, path filters, budget, report as artifact and comment
config/model_routing.yaml  config/verify.yaml  config/evals.yaml
migrations/  tests/llm/  tests/verify/  tests/evals/  tests/fakes/  tests/fixtures/synthetic/
docs/routing/ (DECISIONS.md, benchmark reports)  docs/verify/  docs/evals/  docs/adr/
```

### 4.1 Routing flow
`call(task_kind, prompt_id, input, schema, attempt)` resolves the tier from config, selects the candidate by the ladder for this attempt and by health and eligibility (R4, R8), builds the deterministic prefix (R10), checks the budget (R9), calls through the adapter, validates the output (R3), writes the ledger row and call record (tokens, cache tokens, cost, alias, `degraded`), and returns typed output or a typed error for the node `RetryPolicy` to retry. The router holds no retry loop.

### 4.2 Verification flow
After `distill_grade` and the support judge: (1) the **claims verifier** processes every Confirmed claim in source batches (V1 to V4, V8, V9); (2) the **figures check** recomputes every figure against its base (V5); (3) the **narrative reread** runs after the narrative exists and before the build (V6). Each result flows through `policy.py`, which asks the grading engine for downgrades and the tracker and run log for events; the build reads only post-verification rows.

### 4.3 Eval flow
`runner` executes the real pipeline on each golden account against cassette providers and either recorded or live models, `metrics` computes the exact definitions from stored rows, `stats` and `gates` compare with the immutable baseline and the thresholds, `report` writes a deterministic markdown and JSON report, and CI marks the required check. `bench` reuses the runner with a routing matrix (candidate per task kind) to produce the benchmark report that D-48 and D-51 require.

## 5. Database (one expand-only revision; follow `scripts/lint_migrations.py` rules R1 to R5 including `lint:covers`)
- Ledger rows for model calls extended with `task_kind`, `tier`, `model_alias`, `provider_code`, `routing_version`, `prompt_hash`, `degraded`, `degraded_reason`, token counts including cache read and write (add columns only).
- `llm_candidate_health` (mutable, documented as shared circuit state, not a ledger).
- `verifier_findings` (insert-only): claim, source text hash, outcome (confirmed, value_mismatch, not_found, unreadable, unavailable, not_independent), action, verifier alias, prompt hash, span hashes, run.
- `eval_runs`, `eval_case_results`, `eval_metrics`, `eval_baselines` (insert-only), under a dedicated synthetic evaluation tenant so forced RLS still applies.
- Forced RLS, policies `TO app_rw`, no UPDATE or DELETE on insert-only tables; tenant views cannot read verifier findings, aliases or eval tables.

## 6. Loops (all bounded, recorded, testable)
- **Schema re-ask:** at most 1, then a typed error. **Attempts:** owned by the node `RetryPolicy`; the router only selects per attempt.
- **Verification batches:** bounded by the token share and the claim count; exit reasons `converged`, `budget`, `unavailable`; unverified claims are capped.
- **Eval repeats:** k per config; **bootstrap resamples** fixed count and seed; **live budget cap** stops the run and fails it; **bench matrix** size capped.
- **Health:** one state change per window; cooldown bounded; no loop. Every loop records its exit reason and appears in `docs/architecture/loops.md`.

## 7. Integration
1. `LlmPort` is the only model entry; call sites migrate to typed task kinds; the tier config moves into `config/model_routing.yaml`; graph and workflow stay unchanged except for the verification stage.
2. The verifier extends `s10b_judge_verify` and calls the grading engine's downgrade API; the support judge stays and shares the independence check.
3. CI: `.github/workflows/eval.yml` as a required check with path filters (prompts, routing config, agent and grading code), `replay` on every PR, `live` when the filters fire, `nightly` on a schedule; cloud access through workload identity, no long-lived keys; the report is an artifact and a PR comment; branch protection documented.
4. Metrics by task kind, tier, outcome and degraded flag only (no tenant label, no prompt or document text); OTel spans per model call with the run id as trace id, tokens, cache tokens, cost and alias. Cache-ratio alerting is defined in the observability work package.
5. **Comments:** every module opens with a docstring (purpose, guarantees, failure modes) and each guarantee is tagged `GUARANTEE(<test name>)`, per the master audit prompt.

## 8. Test harness (build before the code)
Location `tests/llm/`, `tests/verify/`, `tests/evals/`, real Postgres, injected `Clock`, fakes from `tests/fakes/`, fixtures labelled `synthetic`.
1. **Scripted model fake:** keyed by task kind and input hash; returns valid output, schema-invalid output, wrong types, fabricated quotes and values, upgrade attempts, a claim echoed back, prompt-injection text, the canary string, over-long output, and errors (429, 5xx, timeout, model unavailable, context too long, authentication, content refusal).
2. **Routing tests:** the task to tier map, the ladder per attempt, health changes, fallback only on the allowed classes, no fallback on schema or authentication errors, degradation recorded, data-class refusal, budget refusal, replay without paying twice, deterministic prefix bytes, a static scan finds no model id and no untyped call site.
3. **Verifier tests (hand-written oracles):** a correct Confirmed claim confirms; a wrong value, an absent value and a source that says the opposite downgrade; a verifier that returns a fabricated quote, a quote not in the source, the claimed value echoed without a quote, or an attempt to upgrade fails closed; an unavailable, over-budget, same-family or unreadable verifier caps the claim; figure mismatches downgrade; narrative verdicts remove or re-phrase by template and never insert model text; resume replays verdicts at no cost; injection text in a source changes nothing.
4. **Property tests (hypothesis):** `policy.py` never produces an upgrade; downgrades are monotone; the router's choice is a pure function of config, attempt and health; the metrics are invariant to case order; statistics return values inside their bounds; the number registry comparison is exact.
5. **Metric and statistics known-answer tests:** hand-computed coverage, citation validity, precision and recall, calibration error and unsupported-claim rate on small labelled sets; Wilson and bootstrap against reference values; Holm correction on constructed p-values.
6. **Harness self-tests (E10):** **A/A runs** (identical candidate and baseline) block less often than the configured false-block rate over many seeds; **seeded regressions** (degrade a prompt, drop a citation, inflate a grade, break a quote check, route a task to a weaker candidate) each block; an improvement passes; a noise-only change does not block; a missing mode, an exceeded budget and an infrastructure error block.
7. **Golden and contamination:** the loader refuses a label without a capture date or split; stale labels are excluded and counted; a label planted in a prompt fails the contamination check.
8. **Security evals:** the injection corpus and canary tests pass at 100% through the verifier and the extractor; a poisoned page cannot change a grade upward.
9. **Isolation and database tests:** two tenants never see each other's findings or call records; insert-only tables refuse UPDATE and DELETE; the migration runs twice cleanly and passes the lint.
10. **Whole-run system tests:** a synthetic account with fabricated claims runs end to end; every fabricated Confirmed claim is downgraded or dropped, the narrative contains no unsupported sentence, and the report counts the disagreements.
11. **Scale smoke:** 5,000 claims verified in batches within a memory cap and a token cap; measure and report, do not assert numbers I did not give.
12. **Mutation check:** run `mutmut` (or the project's tool) on `routing`, `policy`, `metrics`, `stats`, `gates`, `contamination`; list survivors or say it could not run.
Coverage: at least 90% on the three packages, **100% on `routing` (selection logic), `schema`, `policy`, `figures` (comparison), `metrics`, `stats`, `gates` and `contamination`**.

## 9. The benchmark (decision data, never invented)
`bench.py` runs the routing matrix over the golden accounts and writes `docs/routing/benchmark-<id>.md` with per task kind and candidate: coverage, citation validity, grade accuracy, unsupported-claim rate, verifier disagreement rate, cost and time, each with intervals, plus a **proposed** routing diff. The report ships with an empty results table and the status `Blocked: needs analyst-labelled golden accounts, real model access and credits`. Nothing in `config/model_routing.yaml` is changed from it without D-51.

## 10. Development loop (bounded)
For each component repeat at most **5 iterations**: write or extend failing tests; smallest implementation; run its tests, `ruff`, `mypy --strict src/llm src/verify src/evals` and the isolation suite; if red, change the **code**. After 5 red iterations on one component, STOP and report the failing test, its output, what you tried and your hypothesis. Do not weaken the test, mock the unit under test, skip it, or mark it `xfail` (except for a defect already recorded in `references/13` or an ADR).
Build order: `config`, `errors`, `models`, `stats`, `metrics`, `gates`, `golden`, `contamination`, scripted fakes and oracles, `schema`, `routing` and `health`, `budget`, `cost`, `cache`, adapters, `policy`, `figures`, `claims`, `narrative`, `stage`, migration, `runner`, `baseline`, `report`, `calibrate`, `adversarial`, `bench`, CI workflow, docs.

## 11. Production requirements
Type hints and `mypy --strict`; no bare `except`; no global mutable state; no clock or randomness in pure modules (seeded randomness only in statistics); typed errors; config validated and hashed at startup; prompts and document text never logged at info level (canary test); secrets only through Secret Manager references; idempotent migration; every public function documented with its guarantee; every state change a single conditional `UPDATE ... WHERE status = <expected>` or an insert.

## 12. Gates before you finish
Run and report real results: skill self-test; `validate_assets.py`; `lint_migrations.py`; `check_rls_catalog.sql` (no rows); `ruff check`; `mypy --strict`; isolation suite; `pytest tests/llm tests/verify tests/evals` with coverage; the full `pytest -m "not eval"`; the replay eval over the synthetic golden set with its report; the static scans (no model id, no untyped call site, no LLM import outside `src/llm`). Confirm: every config entry has a `source` or `status`; every R, V and E rule has a passing lying or seeded test; the A/A suite and seeded regressions behave as specified; the docs and `loops.md` are current.

## 13. Deliverables
`src/llm/`, `src/verify/`, `src/evals/`, the migration, the three config files, `.github/workflows/eval.yml`, `docs/routing/` (the routing guide, `DECISIONS.md`, the benchmark template), `docs/verify/` (the verification protocol and finding codes), `docs/evals/` (metric definitions, the statistics note, the labelling guide for analysts, the golden file format, the CI and budget runbook), ADRs (fallback as selection, blind verification and downgrade-only, eval modes, governance), the updated open-decision register (D-48 to D-51, the new conflicts), tests and fixtures, updated `BUILD_LOG.md` and `docs/STATUS.md`.

## 14. Stop rules
Stop and ask me if: the grading engine's downgrade API or the workflow judge stage is missing; golden labels or real model access are needed for a decision (build the harness, list exactly what to supply); a source and the roadmap disagree outside the register; the independence rule cannot be met with the available candidates (list the options); a change would drop or alter an existing column; real credentials, spend or tenant data would be needed; a gate stays red after 5 iterations.

## 15. Final reply (short)
A table of components (router, health, schema, cache, budget, adapters, claims verifier, figures check, narrative reread, golden loader, metrics, statistics, gates, runner, baseline, calibration, adversarial suite, bench, CI check: Done, Partial, Blocked, and what each depends on); gates run with results; what is **proven by tests** versus assumed (state precisely: fallback is selection with one retry owner, no model id in code, judge independence, verifier can only downgrade, unverifiable Confirmed is capped, figures checked in code, metrics exactly defined, A/A false-block rate bounded, seeded regressions block); what is **not proven** (real model quality and cost, real provider fallback behaviour, real golden-set scores, the live URL check, the real merge-block on the repository platform); the decisions I must make (D-48 to D-51); what I must supply (analyst-labelled golden accounts with capture dates, real model access and credits, candidate prices and terms with sources, thresholds to confirm, the zero-retention verification V5); follow-ups (shadow grading of live runs, cache-ratio alerts in observability, tenant-visible quality metrics); open `[VERIFY]` items; commits; the next work package.
