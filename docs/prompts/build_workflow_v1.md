# Prompt: build Workflow v1 (the 13 steps of a run as durable workflow stages), production grade

Paste everything below the line into Claude Code, opened in the project repo (built with the `account-intelligence-platform` skill).

---

Build **Workflow v1**: the 13 steps of a run from PRD section 4.2 (set-up, pass 1, baseline, news, jobs, tech, people, name searches, verification, distill, build, gate, delivery) as workflow stages. Stages run **sequentially** in v1; the design must let Phase 2 turn on parallelism without a rewrite. Write production-grade code and prove it with a real harness. Never invent facts about the process, the providers or the account.

## 0. Ground rules

1. Read `.claude/skills/account-intelligence-platform/SKILL.md` first and obey it. Then read `references/01-product-and-process.md` (the 13 steps, rules, gate), `references/04-run-lifecycle-and-agents.md`, `references/06-evidence-and-grading.md`, `references/03-tenancy-isolation-security.md`, `references/13-starter-code-and-known-defects.md`, `assets/checklist.yaml`, `assets/grading.yaml`, `assets/routing.yaml`, `BUILD_LOG.md`, `docs/STATUS.md`. If `docs/sources/` holds the original PRD, read section 4.2 and Appendix A there too and cite it. Follow the skill's work loop: plan in five lines, tests first, smallest increment, run the gates, log.
2. **Architecture is fixed by the skill:** a LangGraph `StateGraph` whose Postgres checkpoint is the durability source of truth; the Inngest function only triggers, schedules and loops 240-second slices; one retry owner per failure class; tenant from the token only; no SQL tool for agents; untrusted text read only by the tool-less extractor. Do not change these. Temporal is only a fallback if Phase 0 spike 2 failed (check `docs/decisions/`).
3. No real provider calls, no real LLM calls, no real spend, no real tenant data while building. Use fakes and recorded fixtures. A `live` test may exist, skipped by default, needing an explicit variable and a spend cap.
4. Never claim a gate passed that you did not run. Never weaken or delete a test to pass. Commit on branch `feat/workflow-v1` in small Conventional Commits. Do not push.

## 1. Preflight (before writing workflow code)

Check what exists and print a table: data layer repositories (sources with stable codes, facts plus evidence write path, people, postings, detections, coverage rows), raw payload store, broker connectors (`call_operation`, `retry_policy_for`), ledger with lease and intent row, budget gate, grading engine (`src/agents/grading.py`), wallet settlement, the starter graph and runner, the Inngest loop.
- For each missing dependency, define a **port** (a typed `Protocol`) in `src/workflow/ports.py` and an in-memory fake in `tests/fakes/`. Build the workflow against ports so it can be completed and tested now.
- Do **not** rebuild a whole missing work package (data layer, connectors, deliverable builders, wallet). Write the port, the fake, and list the gap in the final report. Production adapters that do not exist yet raise `NotConfigured`, which ends the run **PARTIAL with a reason**, never a fake success.
- Exception: `finalize` (settlement at every terminal state) is part of this work. Implement it against the wallet tables if no settlement exists, with the invariants in section 7.4.

## 2. Anti-hallucination protocol (build time)

1. The 13 steps and their rules come from `references/01` and the PRD. Do not add steps, rules, lane keywords, title lists, seniority bands or thresholds from memory. Anything the sources do not give is configuration marked `source: TODO, needs product owner` and listed in the final report.
2. **Stage map is derived, not invented.** Create `config/workflow_v1.yaml` and `docs/workflow/stage-map.md` assigning each of the 33 coverage rows in `assets/checklist.yaml` to exactly one owning stage, using the PRD Appendix A labels and each row's `pass`. Rows whose owner is ambiguous are listed under "proposed, needs confirmation". A test asserts **every row has exactly one owner** (no silent omission) and no stage owns a row that does not exist.
3. The PRD verification step (9) comes **before** distill (10), while Architecture 4.x puts the judge `verify` node **after** `distill_grade`. Resolve it this way and record it in an ADR: step 9 is the three-query lead-verification loop; step 10 is `distill_grade` followed by a judge pass `judge_verify` inside the same stage group (the Architecture `verify` node). The judge model must differ from the generator.
4. Prompts are versioned files under `src/agents/prompts/`; their content hash is recorded on the run with `graph_version`. No model IDs in code; tiers (planner, extractor, judge) resolve from config. Do not copy prompt wording from memory of other systems.

## 3. Anti-hallucination protocol (run time): the evidence gate

Every stage output passes through `EvidenceGate` before it reaches the database. These rules are code, not prompt text:
1. **Evidence or drop.** A fact needs at least one evidence item `(source_code, quote, retrieved_at)`. No evidence, no row. The drop is logged and counted, never silent.
2. **Quote check (deterministic).** A claimed quote must appear verbatim (after whitespace and Unicode normalisation) in the stored raw source it cites. A claimed figure must appear in the cited source text with the same number and unit after normalisation. Failing either: drop the claim, record `dropped: unverifiable_quote`, and downgrade or Block the owning coverage row if it ends empty.
3. **URL check.** A cited URL must exist among this run's stored sources and resolve to the domain the source claimed. Models cannot introduce a URL that no connector returned.
4. **Grades are computed, never asked.** The model proposes attribute values and evidence only. Code assigns engine grade (`grading.py`) and product grade (skill proposal for D-01, labelled). **Confirmed requires a primary-source read this run** (flag set by code from the fetch record). Absence of evidence is Hypothesis. A keyword match or a date range is a Lead.
5. **Hypotheses carry a confirming question**; a hypothesis without one fails validation.
6. **Narrative check.** Every sentence of the Account Summary narrative cites at least one source code that resolves to a graded row, and every number in it appears in a cited row. A sentence that fails is removed or the gate fails; the narrative is generated from graded rows only, never from raw text.
7. **Schema-validated model output.** Every LLM call uses a pydantic output schema, fixed temperature and token cap per node. Invalid output is not repaired by a second guess: one bounded re-ask with the validation error (max 1), then the item is Blocked with a reason.
8. **Quarantine.** Raw pages, postings and profiles reach only the tool-less extractor. The planner and tool-calling nodes see validated structured facts only. The extractor carries a canary instruction; any output echoing it flags the source hostile and drops it. Extracted strings are length-capped and stripped of control characters.
9. **Judge pass.** For every fact graded A, B or Confirmed, the judge checks that the cited span supports the claim. Unsupported claims are downgraded or dropped and the change is recorded.
10. **Sensitive findings** (breach, layoff, lawsuit) are stored with a `preparation_only` flag; deliverable text must say so. For Salesforce partner clients nothing is framed as a customer leaving Salesforce.

## 4. Architecture to build

```
src/workflow/
  spec.py          WorkflowSpec / StageSpec loaded from config/workflow_v1.yaml (typed, validated, acyclic)
  state.py         RunState (pydantic or TypedDict), schema_version, migrate(), reducers on every key written by more than one node
  ports.py         Protocols: ConnectorPort, LlmPort, SourceRepo, FactRepo, PeopleRepo, PostingRepo, DetectionRepo, CoverageRepo, PayloadStore, BuilderPort, GatePort, WalletPort, Clock
  stage.py         Stage base: idempotent run(), pre/post conditions, timeout, owned coverage rows, allowed tools
  stages/          s01_setup.py ... s13_deliver.py (+ s10b_judge_verify.py)
  evidence_gate.py section 3
  coverage.py      tracker state machine: Not done, Done, Partial, Blocked, None; proof required
  budget_view.py   read-only view of the run budget; hard checks happen in the connector layer
  engine.py        builds the LangGraph StateGraph from the spec; attaches retry_policy_for(); sequential edges in v1
  runner.py        time-sliced, resumable execution (extends the starter runner)
  transitions.py   run state machine with optimistic UPDATE ... WHERE status = expected
  finalize.py      settlement and run closure
src/orchestrator/functions.py    Inngest loop: slice, approval wait, timeout path, finalizer
config/workflow_v1.yaml          stages, dependencies, tools allowed, budget share, loop caps, coverage ownership
```

**Spec-driven and parallel-ready.** Stages, `depends_on`, allowed tool categories, per-stage soft budget share and loop caps live in `config/workflow_v1.yaml`. `engine.py` builds the graph from it. v1 runs in topological order with concurrency 1. A test proves the dependency graph is acyclic and encodes the PRD dependencies (for example: name searches after people; profile pulls after the title search; verification after all source stages; distill after verification; judge after distill; build after judge; gate after build; delivery last). Every state key that more than one future parallel branch could write already has a reducer (known defect 1 must never recur).

**State stays small.** The checkpoint holds ids, counters, status and references (`raw_ref`, `source_code`), never page text or full payloads. A test caps serialised state size and fails if a stage puts large text in it.

**Stage contract.** Every stage: declares owned coverage rows and allowed tools; is **idempotent** (running it twice from the same checkpoint produces the same state and zero extra paid calls); checks its preconditions and fails fast with a typed error; writes through repositories only; ends by setting each owned coverage row to Done, Partial, Not done, Blocked or None **with proof** (queries, tool category, result count); emits a span and structured log with stage name, duration, calls, cost, outcome.

## 5. The 13 stages (sources: `references/01`; do not embellish)

| # | Stage | Must do | Loop bounds |
| --- | --- | --- | --- |
| 1 | `setup` | Confirm and resolve the domain; resolve the careers URL **by visiting it** (never construct it) and store `careers_verified_at`; record the pursuing client if supplied, else mark relationship rows "waiting on the account owner" once and never ask again; check broker balances (free); compute one cap with a breakdown; **approval gate**: interrupt to `AWAITING_APPROVAL` unless the tenant policy auto-approves within the reserved budget. Approval timeout is 24 h, then cancel and settle | none |
| 2 | `pass1_relationship` | Prior customer status and interactions (client history port; Phase B), alumni and connection overlaps, platform vendor account team (named or marked unknown), profiles of obvious key contacts, basic fit check. Without the client history port: rows Not done with reason, not guessed | none |
| 3 | `baseline` | Last three earnings releases or equivalent, newsroom, leadership page; executive voice from the last two earnings call transcripts (speaker named). **Every quoted figure is re-read in its release** (quote check, section 3.2) | bounded by tool cap |
| 4 | `news` | Company releases first, then the structured feed (pulled once), then targeted searches for items the feed hints at, phrased as a journalist would; verify important items at the source; unopened feed items are **Feed only** | query cap per stage |
| 5 | `jobs` | One live board search per lane keyword; 12-month pull of open and closed postings; read descriptions locally; platform sentence in every posting that names a platform; **full text for every role where the platform is the job**; classify Build role, Admin and owner role, Working tool, User role (deterministic rules first, extractor only for the ambiguous remainder); counts with base (`read`, `matched`, `open`, `closed`) per platform | pagination caps from the connector layer |
| 6 | `tech` | Detections pulled once; lane-relevant rows kept with first and last seen; cross-checked against postings; **a date range alone is a Lead** | none |
| 7 | `people` | Title search on the domain (platform ownership titles plus leadership titles, two seniority passes, paged on the same source); association search only to fill gaps; people named in research with profile URLs; merge by profile URL first, name second; apply exclusions (security, IT compliance, IT audit; scope from the run input); level, lane, priority; full profile pull for every senior person; posts and comments for **priority A only**, recent activity | page caps; dedupe |
| 8 | `name_searches` | After the people list exists, for the top ten: speaking, podcasts, video, articles, vendor customer stories; review sites and communities checked and recorded **None** where nothing is found | 10 people cap |
| 9 | `verification` | Every Lead gets **three differently worded queries across at least two tool categories** before it stays a Lead; parked leads are searched again once when later evidence supports them | 3 queries per lead; 1 re-pass |
| 10 | `distill_grade` | Grade every line (code); hypotheses written as hypotheses with confirming questions; first-call questions; "what changes a decision". Then `judge_verify` (section 3.9) | planner gap loop at most 2 |
| 11 | `build` | Call `BuilderPort` to generate the standing file set from stored rows | none |
| 12 | `gate` | Call `GatePort` (lost rows, orphan citations, banned dashes, stale phrases, excluded names, formula errors, tracker tally recomputed from rows, narrative versus tables, broker-name scan). **Fix, rebuild, rerun** at most 3 cycles; a persistent failure ends the run PARTIAL with the failing checks listed, never COMPLETED | 3 cycles |
| 13 | `deliver` | Deliver once: what was found, what was corrected during checking, what is still open, credits used against the cap; `finalize` settles the wallet | none |

Rules that cut across stages (enforce in code): a run cannot reach COMPLETED while a High coverage row lacks a status and proof; a failed or rate-limited source marks its rows Blocked with a reason and the run continues; **pull once, work from stored rows** (stages read the payload store, never re-call a connector for something already pulled); another account's name never appears in an account's output; no outreach code path exists.

## 6. Loops (all bounded, recorded, testable)

- **Slice loop:** 240 s slices, at most 60 (4 h), per the skill. A timeout at a slice boundary loses at most the node in flight; the ledger replays its paid calls.
- **Planner gap loop:** back to planning at most **2** times; each pass must add new coverage or the loop ends (no-progress detection: if a pass adds no new evidence, stop).
- **Lead verification loop:** at most 3 queries per Lead. "Differently worded" is checked in code (normalised token-set similarity below a configured threshold against earlier queries for the same Lead) and the tool categories must span at least two. A repeated query is rejected, not counted.
- **Gate fix loop:** at most 3 build, gate, rebuild cycles.
- **Retries:** broker 429 and 5xx are retried only by the LangGraph node `RetryPolicy` from `retry_policy_for()`; the workflow adds **no** second retry layer. Poison input and schema failures are not retried (fact Blocked with reason).
- **Budget and call caps:** $5.00 broker cap per run, 60 tool calls, per-stage soft share from config. Hitting a cap stops the fan-out, still runs `distill_grade`, and ends `BUDGET_EXCEEDED` with partial results.
- Every loop writes its exit reason into the state (`converged`, `cap_reached`, `no_progress`, `budget`, `deadline`) so reports and tests can see why it stopped.
- **Cancellation:** the runner checks the run status between supersteps; `CANCELED` exits cleanly and settles what was spent.

## 7. Durability and state machine

1. **Run states:** `QUEUED, RUNNING, AWAITING_APPROVAL, DISTILLING, VERIFYING, COMPLETED, PARTIAL, BUDGET_EXCEEDED, FAILED, CANCELED`. Every transition is `UPDATE ... SET status = :new WHERE id = :id AND status = :expected`; zero rows means the transition lost a race and is a no-op. Illegal transitions raise. A test enumerates the transition table.
2. **Resume:** `execute_slice` resumes from the checkpoint; a finished run returns without effect; the graph version is pinned at creation and a run always resumes on its own version; `schema_version` plus `migrate()` upgrade old state on load (test with a v1 fixture checkpoint).
3. **Inngest loop:** keep the starter shape; fix known defect 8 (on approval timeout, cancel the run and settle with a refund, do not loop into the same interrupt); handle the slice ceiling by settling and refunding through `finalize`. Verify every Inngest SDK call against the installed package (verification item V4); do not guess signatures; mark unverified behaviour `# VERIFY(V4)`.
4. **`finalize` invariants:** each terminal state produces exactly one settlement (commit the actual cost, refund the remainder; full refund when the platform was at fault); idempotent under duplicate delivery; ledger insert-only; no negative balance; `reserved - committed - refunded = 0`. Property and concurrency tests cover duplicate and racing finalisation.
5. **Isolation:** all DB access through `tenant_txn`; the graph state carries `tenant_id` and `run_id` from the token path only; no node, tool or model output can set them.
6. **Observability:** run id is the trace id; one span per stage and per tool or model call; metrics by stage, outcome and plan tier only (no tenant label); logs through the allow-list; prompts and document text never logged (a canary test).
7. **LLM cost layout:** stable cached prefix with no tenant data, tools sorted, canonical JSON, no timestamps in the prefix; record cache-read, cache-write and uncached tokens per call. Model IDs, prices and cache limits are configuration marked `[VERIFY]`.

## 8. Test harness (build it before the stages)

Location `tests/workflow/`; real Postgres (the project's test DB) with injected `Clock`, sleep and random.

1. **FakeLlm:** scripted and deterministic (keyed by prompt-template id and input hash), able to return valid output, schema-invalid output, **fabricated quotes, fabricated URLs, inflated grades, unsupported claims, extra tool-call requests, prompt-injection echoes and the canary string**. Every hallucination case must be caught by the evidence gate, the schema check or the judge pass.
2. **Fake connectors and payload store** from synthetic accounts under `tests/fixtures/synthetic/` (each file named and flagged `synthetic`): one rich account, one sparse account (many rows None), one hostile account (injection pages), one with a provider outage, one with conflicting sources.
3. **Stage unit tests:** preconditions, postconditions, owned coverage rows, proof recorded, idempotency (run twice, assert identical state and zero extra paid calls).
4. **Whole-run golden tests:** sequential run of the rich account to COMPLETED with the expected tracker, facts, grades and delivery note; sparse account completes with Hypothesis and None, never invented facts; hostile account drops injected content and raises the alert; outage account ends PARTIAL with Blocked rows and reasons.
5. **Resume matrix:** parametrised over every stage and three kill points (before the stage's first call, between a provider response and ledger finalisation, after the stage but before the checkpoint). After resuming, assert the **same final state hash**, no duplicate paid call beyond the documented one-repeat bound, and a ledger sum equal to `spent_usd`.
6. **Property tests (hypothesis):** the transition table; coverage ownership is a total function; loops always terminate within caps for any generated outcome sequence; "differently worded" detector rejects near-duplicates; state size cap; reducers merge parallel writes without loss.
7. **Budget and cancel tests:** cap hit mid-stage gives `BUDGET_EXCEEDED` with distill still run and settlement correct; cancel mid-run settles spend so far; approval timeout cancels and refunds.
8. **Quality-gate wiring tests:** a seeded defect (orphan citation, banned dash, missing High row, broker name in output) blocks COMPLETED.
9. **Isolation:** run two tenants concurrently on the same domain; no ledger, fact or checkpoint row crosses; extend `tests/isolation` for any new table.
10. **Determinism:** two identical runs with the same fakes produce identical state hashes and identical deliverable inputs.
Coverage target: at least 90% on `src/workflow/`, 100% on `evidence_gate.py`, `coverage.py`, `transitions.py`, `finalize.py`.

## 9. Development loop (bounded)

For each component repeat at most **5 iterations**: write or extend failing tests; smallest implementation; run that component's tests, `ruff`, `mypy --strict src/workflow` and the isolation suite; if red, change the **code** and rerun. After 5 red iterations on one component, STOP and report the failing test, its output, what you tried and your hypothesis. Do not weaken the test, mock the unit under test, skip it, or mark it `xfail` (except to document a defect already recorded in `references/13` or an ADR).
Build order: `spec`, `state`, `transitions`, `coverage`, `evidence_gate`, `ports` and fakes, FakeLlm and fixtures, `stage` base, stages 13 down to 1 only if their ports are faked first (otherwise 1 to 13), `engine`, `runner`, `finalize`, Inngest loop, whole-run and resume matrix tests.

## 10. Gates before you finish

Run and report real results: skill self-test; `validate_assets.py`; `lint_migrations.py`; `check_rls_catalog.sql` (no rows); `ruff check`; `mypy --strict src/workflow`; isolation suite; `pytest tests/workflow` with coverage; full `pytest -m "not eval"`. Confirm: every checklist row has exactly one owning stage; no model ID or vendor name in `src/workflow/`; every hallucination case in FakeLlm is caught; the Inngest SDK usage is verified or marked `# VERIFY(V4)`.

## 11. Deliverables

`src/workflow/`, updated `src/orchestrator/functions.py`, `config/workflow_v1.yaml`, `docs/workflow/stage-map.md`, ADRs (verification and judge ordering, port strategy, approval policy), prompts under `src/agents/prompts/`, tests and fixtures, updated `BUILD_LOG.md` and `docs/STATUS.md`.

## 12. Stop rules

Stop and ask me if: an owner for a coverage row cannot be derived from the PRD and affects a High row; a decision from D-01 to D-11 blocks you and has no default; a change would drop or alter an existing column; you want to alter the architecture in section 0.2; real credentials, spend or tenant data would be needed; a gate stays red after 5 iterations; or the skill and another source conflict outside the register.

## 13. Final reply (short)

A table of the 13 stages (Done, Partial, Blocked, and what it depends on); the missing ports and which work package supplies them; gates run with results; what is **proven by tests** versus assumed (state the durability, settlement and hallucination-guard guarantees precisely); rows with proposed owners needing my confirmation; open `[VERIFY]` items; commits; the next work package.
