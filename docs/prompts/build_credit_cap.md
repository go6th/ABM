# Prompt: build Credit cap v1 (one cap per run, per-broker spend tracking, end-of-run spend report), production grade

Paste everything below the line into Claude Code, opened in the project repo (built with the `account-intelligence-platform` skill).

---

Build **Credit cap v1**: **one cap per run** across all paid tools, agreed up front and enforced before every paid call; **per-broker spend tracking** derived from the ledger; and an **end-of-run spend report**. This is backend work. It sits on top of the broker connector layer (which already reserves cost atomically per call) and adds the cap itself: proposal, approval, enforcement policy, extension, reconciliation and reporting. Write production-grade code and prove it with a real harness. Never invent prices, rates, limits or process rules, and never let a model choose, approve, extend or report money.

## 0. Ground rules

1. Read `.claude/skills/account-intelligence-platform/SKILL.md` first and obey it (rules 6, 7, 10, 11 and 12 bind here). Then read `references/01-product-and-process.md` (setup step, constraints), `references/04-run-lifecycle-and-agents.md` (ledger, budgets, run states), `references/08-api-auth-billing.md` (reserve, commit, refund, settlement), `references/10-testing-evals-cicd-ops-cost.md`, `references/12-open-decisions-and-verification.md` (D-04, D-10, V7), `references/13-starter-code-and-known-defects.md` (defects 1, 3, 4, 7), `assets/routing.yaml`, `assets/checklist.yaml`, `scripts/scan_client_output.py`, `BUILD_LOG.md`, `docs/STATUS.md`. If `docs/sources/` holds the PRD and SaaS document, cite PRD 3.1 (goal 5), 3.3 (inputs: credit cap), 3.5 (constraints), 3.6 (metrics), 4.2 steps 1 and 13, 4.6 (cost model); SaaS section 6.
2. **What the sources say (the whole rule set; do not add to it as fact):**
   - One cap per run: "one number across all tools with a short breakdown; within the cap the run does not stop to ask" (PRD).
   - Broker balances are checked before the run, and the check is free; the cap is proposed with a breakdown and the run **waits for a yes** (PRD step 1).
   - **Ask before spending beyond the agreed cap; report all spend at the end**; spend within the cap, with variance reported (PRD constraints and metrics).
   - Hitting the budget ends in `BUDGET_EXCEEDED` with partial results, commits up to the cap and refunds the rest; the budget guard runs **inside the graph, before the call**; default broker cap **$5.00 hard** per run and **60 tool calls** (ARCH via references 04 and 08).
   - Credits and dollars never share a field (defect 4); money is an insert-only ledger; one settlement per terminal run.
   - Client-facing files never name a broker or a credit count; the SaaS portal shows **credits and categories, never vendors**.
3. **Fixed by the skill:** the database enforces isolation (`tenant_id NOT NULL`, forced RLS, roles `TO app_rw`, `tenant_txn`); tenant identity comes only from the verified token; agents get no tool that can set, approve or extend a cap; never send outreach; never delete or overwrite (cap changes are new versions); client-facing output attributed to "6th agents".
4. No real provider calls, no real spend, no real credentials, no real tenant data. Fakes, `synthetic` fixtures, hermetic containers only.
5. Never claim a gate passed that you did not run. Never weaken or delete a test to pass. Commit on branch `feat/credit-cap` in small Conventional Commits. Do not push.

## 1. Preflight (before writing code)

1. Print a table of what exists: connector `budget.py`, `ledger.py`, `cost.py`, `config/providers.yaml`, the `tool_calls` table and its columns (`provider_code`, `operation`, `route`, `credits`, `cost_usd`, `cost_source`, `possible_duplicate`), the runs table and its columns, the workflow `setup` stage and approval gate, `finalize` and the settlement code, tracker `view` and its `blocked` writer, `PrincipalPort` or equivalent, the operator console surface, a nightly job runner, `tenant_txn`, the isolation suite.
2. **Table naming conflict:** the data layer prompt defined `runs.credit_cap` (integer) while the starter uses `account_runs.budget_usd`. Do **not** create a third. Use the table the workflow actually uses, map the other in an ADR, and add expand-only columns. Never drop or alter an existing column (ask first if unavoidable).
3. Missing dependency: define a typed `Protocol` in `src/spend/ports.py` and an in-memory fake in `tests/fakes/`: `BalancePort` (free balance check per provider), `CostConfig`, `PlanPort` (the pending coverage operations with priority), `SettlementPort`, `PrincipalPort`, `TrackerPort` (mark rows Blocked with reason), `PrepulledStore`, `Clock`, `Notifier` (operator prompt). Adapters that do not exist raise `NotConfigured`. **Fail closed:** with no approved cap there are **no paid calls**; free operations proceed and the affected rows are Blocked with reason `no_cap`.
4. **One implementation per rule.** The connector's atomic `UPDATE ... WHERE spent + est <= budget` stays the only place spend is incremented. This package never increments a counter itself; it decides, records, reconciles and reports. If cap code already exists elsewhere, move it here and leave a thin re-export.

## 2. Anti-hallucination protocol (build time)

1. **Prices, rates and limits are data, not code.** `config/spend.yaml` and `config/providers.yaml` hold every number with `source` (document and section, or a supplied file), or `source: TODO, needs product owner`, or `status: proposal`. Known from the sources: plain page scrape 1 credit, JSON extraction 5, search-with-scrape 20 or more, people-by-technology 1 credit per person plus per attribute, people by title no visible cost on the current plan, news and detection feeds "credits per call" (amount not given), profile enrichment "estimate on a sample first, then confirm". **Not given anywhere:** any provider's credit-to-USD rate (V7), the per-call price of feeds, whether each provider has a free balance endpoint, headroom over an estimate, extension limits, approval timeouts, who may approve, the tenant auto-approval limit, the variance threshold. All are `null` or `proposal` until supplied. Never treat the PRD's "a few hundred credits" as a default cap or convert it to dollars.
2. **Proposals of mine, not facts from the sources** (config marked `status: proposal`, ADR, register entries at the next free numbers; earlier prompts used D-12 to D-26, so start at **D-27**, and new conflicts at the next free `C` number, unless the register shows otherwise):
   - **D-27 cap unit (conflict: PRD says credits, ARCH says $5.00):** one number per run stored in **USD** as integer micro-dollars; provider credits recorded per call in their own column; an operator may enter the cap in credits **only** where a configured `credit_usd_rate` with a source exists; otherwise USD only.
   - **D-28 beyond the cap (conflict: PRD says ask, ARCH says stop):** attended run (an operator is on it): one **extension request** (bounded count), run waits in `AWAITING_APPROVAL`, decline or timeout ends in `BUDGET_EXCEEDED` with partial results; unattended or scheduled run: `BUDGET_EXCEEDED` at once. Never an automatic extension.
   - **D-29 breakdown and scope:** the breakdown by provider and category is **advisory** (variance reported, never blocks); the total is the only hard limit; **LLM spend is reported but not counted against the cap** (ARCH keeps a separate token cap); the **per-tenant daily cap and the monthly refresh budget are not built here** (ARCH names the first, SaaS the second; no values given), only a `CapPolicyPort` hook.
   - **D-30 spend visibility (conflict: PRD and SaaS put credits used in the delivery note, skill rule 6 forbids a credit count in client output):** the spend report is internal; **no client file ever carries it**; the tenant gets a separate allow-listed view of categories and call counts, with amounts only once D-04 gives a credit price.
   - **D-31 credit exhaustion (conflict: PRD says supply a pre-pulled file rather than fall back to a weaker free source, `routing.yaml` lists fallbacks):** default per the PRD: the row is Blocked `credits_exhausted`, the operator is prompted, and a registered pre-pulled file is consumed instead of a call; fallback providers only where tenant config sets `allow_fallback_on_exhaustion: true` (default false). If the connectors already fall back on exhaustion, put that behind this flag.
3. **Verify every library and SQL fact** against the installed packages and the database version; mark anything unverified `[VERIFY]` in `docs/open-items.md`. Do not state performance numbers; measure them.
4. No vendor or broker name in `src/spend/`, tenant-facing code, config keys, or any client-visible string. Provider identity is a `provider_code`.

## 3. Anti-hallucination protocol (run time): the CapGuard

Code rules, each with at least one "lying input" test (section 9.6).

| # | Rule |
| --- | --- |
| K1 | **One active cap.** Cap versions are append-only (`v1`, then extensions). The enforcement columns on the run equal the latest approved version; a nightly invariant and a test assert it. The cap can never be lowered below spend already made. |
| K2 | **No paid call without an approved cap**, enforced **inside the connector entry** (`call_operation`) as well as in the graph's budget node. A test calls the connector directly with no cap and with a proposed-but-unapproved cap. |
| K3 | **Approval is bound.** It names the proposal hash, run, tenant and a human principal (or a tenant policy within its configured limit), expires, and is single use. A changed proposal, a stale balance snapshot, another tenant's principal, a replay, or any agent or model path fails. No agent tool exists for approving or extending. |
| K4 | **Reserve first, conservatively.** Reserve the configured **upper bound** atomically; `spent <= cap` holds at every instant under concurrency; true up to the actual after the call. The actual is **never clamped**. A cost above its declared upper bound is recorded as `cap_overshoot` (an explicit column), raises an alert, and stops further paid calls. |
| K5 | **Unknown cost is never zero.** No configured hold for an operation means `UnknownCostRefused`. The only way to price an unknown operation is a bounded **sample-estimate step** that is itself a line in the approved breakdown. |
| K6 | **Integer money.** Micro-dollars as integers or `Decimal`, never `float`; costs round **up**; credits are `Decimal` in their own field; conversion only through a sourced rate. A property test sums parts to the total exactly. |
| K7 | **Tracking is derived, not counted twice.** Spend by provider, category, operation and route comes from the insert-only ledger. The sum of the parts equals the run's `spent` and the ledger sum, checked at finalisation and nightly; a mismatch raises `ReconciliationFailed` and the report is stamped `reconciliation_failed`, never shipped silently. |
| K8 | **A replay costs nothing.** Ledger replays, cache hits and pull-once hits add no spend and are counted separately; "avoided spend" is shown only as an **estimate**, labelled so. |
| K9 | **Crash safety.** A stale `started` intent is reconciled on resume. Until it is, its reserved estimate **stays counted** (never under-count), and possible duplicates are listed with the amount at risk. |
| K10 | **Priority under scarcity.** When remaining cap is below the pending plan cost, rows are funded High first (`assets/checklist.yaml` priority), then by a fixed order with a tie-break; every row left unfunded is **Blocked with reason `cap_reached`** in the tracker with its proof, never dropped. |
| K11 | **Within the cap, no interrupt.** After the initial approval a run inside its cap emits zero approval requests. Beyond it, only the extension flow (D-28): append-only version, strictly higher cap, bounded count, explicit attended or unattended mode on the run. |
| K12 | **Terminal settlement once.** Every terminal state, including `FAILED`, `CANCELED` and `BUDGET_EXCEEDED`, produces exactly one report version and one settlement call (commit actual up to the cap, refund the rest). Duplicate delivery and racing finalisation change nothing. |
| K13 | **Visibility.** Client files and the tenant view never contain provider names or codes, per-call data, provider credit counts or another tenant's data. The tenant view is an **allow-list projection**. The operator report cannot enter the export zip (negative test), and its figures are never logged at info level. |
| K14 | **Free must be proven free.** Only operations marked `cost_class: free` with a source bypass the cap. A "free" operation that returns a cost becomes a defect event and is blocked from then on. |
| K15 | **Balances are snapshots.** Each has a timestamp, a source and a status (`ok`, `low`, `empty`, `unknown`), never inferred, and goes stale after a configured age. An empty or unknown provider is excluded from the estimate as a flagged line. Mid-run exhaustion makes rows Blocked `credits_exhausted`; it never fails the run. |
| K16 | **Honest report.** Every figure comes from a rollup with its base; missing data reads `not measured`, never zero; estimated and measured figures are labelled apart; the report is deterministic (same ledger, same bytes). |

## 4. Architecture to build

```
src/spend/
  config.py        load, validate and hash config/spend.yaml; startup fails on an invalid file
  money.py         Micro (int) and Credits (Decimal), rounding, sourced conversion; pure, no floats
  errors.py        CapNotApproved, CapExhausted, ProposalStale, ApprovalInvalid, UnknownCostRefused,
                   ExtensionDenied, BalanceUnknown, ReconciliationFailed, NotConfigured
  models.py        frozen pydantic: CapProposal, BreakdownLine, CapVersion, CapEvent, BalanceSnapshot,
                   ExtensionRequest, SpendLine, SpendReport, TenantSpendView
  estimator.py     pure: plan x cost config -> low, expected, high per line; sample extrapolation with an upper bound
  proposal.py      build the proposal and its hash; minimum viable cap; preview of rows that would be Blocked
  approval.py      cap state machine (proposed, approved, rejected, expired, superseded); binding and expiry
  balances.py      free balance preflight, snapshots, staleness
  gate.py          the single pre-call decision (allow, deny with reason), used by call_operation
  priority.py      pure: rank pending operations against remaining cap (K10)
  extension.py     extension request, approval, decline, timeout
  prepulled.py     register and consume an operator-supplied file (hash, provenance, schema-checked, cost 0)
  reconcile.py     stale intents, ledger versus counters, overshoot, nightly invariants
  rollup.py        spend by provider, category, operation, route, outcome (SQL plus pure)
  report.py        build the operator report and the tenant view; deterministic; pure from rollups
  stage.py         workflow adapters: setup (proposal, gate), budget node, finalize hook
  ports.py
src/api/spend.py   thin router: approve, extend, tenant spend view; operator report behind the operator role
config/spend.yaml  headroom (proposal), rounding step (proposal), timeouts, extension limit, variance threshold,
                   approver roles, auto-approve limit, max snapshot age, allow_fallback_on_exhaustion
migrations/        one expand-only revision (section 6), lint-clean
tests/spend/  tests/fakes/  tests/fixtures/synthetic/spend/
docs/spend/  docs/adr/
```

### 4.1 Setup: proposal and approval (PRD step 1)
1. Take the pending plan from `PlanPort`, run the **free** balance preflight for every provider in the plan, then compute the proposal **in code**: lines per provider and category with low, expected and high; a sample-estimate line for profile enrichment; headroom from config; the cap rounded up to the configured step; the skill's **$5.00 default shown beside it**; the **minimum viable cap** (mandatory operations at low); a preview of the rows that would be Blocked at any lower cap; every assumption and every `unknown` line named.
2. The proposal is stored as an immutable `CapVersion` plus a `proposed` event, its hash computed over canonical JSON. The operator is prompted through `Notifier` and the run enters `AWAITING_APPROVAL`. Approval binds to the hash (K3). A tenant policy may auto-approve only within its configured limit, and that is recorded as the principal. Timeout is configured; on timeout the run is cancelled and settled (known defect 8).
3. The graph's budget node reads only the approved version.

### 4.2 Enforcement (every paid call, before the call)
`gate.check(op, est)`: operation proven free (K14) passes without a cap → a ledger replay short-circuits at zero (K8) → an approved cap must exist (K2) → the operation is priced or refused (K5) → connector's atomic reserve (K4) → on denial raise `CapExhausted` with the remaining amount and the rows affected. The graph catches it, stops the fan-out wave, applies K10 (Blocked rows), distils what exists, and ends `BUDGET_EXCEEDED` unless the extension flow (K11) applies.

### 4.3 Spend report (internal)
Built once per terminal state, versioned, never overwritten. Sections: header (run, account domain, cap versions with approvers and times, final status, attended or unattended); totals (spent, cap, remaining, percent, overshoot, unreconciled); **by provider code** (paid calls, replays, free calls, provider credits, USD, share, advisory allocation, variance, flag); by category and by operation and route; outcomes (ok, empty, failed after charge, blocked); unknown-cost calls; possible duplicates with amount at risk; samples; estimated avoided spend; rows Blocked for `cap_reached`, `credits_exhausted`, `no_cap` (from the tracker, with proof); extensions; free balance re-check at the end versus the start; LLM spend as an informational line; invoice-reconciliation hints. Output is JSON plus a plain Markdown rendering for the operator, **neither is a client file**. The tenant view is the allow-list projection (D-30).

## 5. Loops (all bounded, recorded, testable)
- **Spend waves:** the budget node funds a ranked batch per wave; at most a configured number of waves (a different limit from ARCH's "planner loops 2"; do not conflate them); exit reasons `converged` (all funded), `cap_reached`, `no_progress`, `deadline`.
- **Sample estimate:** one bounded sample round per operation, a sample size cap, an upper bound from observed maximum; no second round without a new approval.
- **Extension:** at most the configured count per run, each needs a fresh approval (K11).
- **Reconcile:** one pass over stale intents per resume and one nightly pass, bounded by a batch size; exit reasons recorded.
- **Approval wait:** owned by the workflow's interrupt, not a poll loop here.
- No retry loop is added in this package: the node `RetryPolicy` stays the retry owner.

## 6. Database (one expand-only revision; follow `scripts/lint_migrations.py` rules R1 to R5 including `lint:covers`)
- `run_cap_versions` (insert-only): `tenant_id`, `run_id`, `version`, `cap_usd numeric(12,6) CHECK (> 0)`, `proposal_json`, `proposal_hash`, `config_hash`, `created_at`; unique `(run_id, version)`.
- `run_cap_events` (insert-only): `tenant_id`, `run_id`, `version`, `event` (proposed, approved, rejected, expired, superseded, extension_requested, extension_declined), `principal`, `reason`, `at`.
- `balance_snapshots` (insert-only): `run_id`, `provider_code`, `status`, `balance`, `unit`, `source`, `taken_at`.
- `spend_reports` (insert-only, versioned): `run_id`, `version`, `status`, `content_hash`, `operator_json`, `tenant_json`, `created_at`.
- `prepulled_files` (insert-only): `run_id`, `provider_code`, `sha256`, `supplied_by`, `object_ref`, `registered_at`.
- Run columns added if missing: `cap_version_id`, `attended`, `cap_overshoot_usd`, `unreconciled_usd`; a `CHECK (spent_usd <= budget_usd + cap_overshoot_usd)` added `NOT VALID` then validated; a view `run_spend_by_provider` over `tool_calls`.
- Forced RLS on every table, policies `TO app_rw`, no UPDATE or DELETE grants on the insert-only tables, roles per the skill. Tenant views are served from a separate role or view that cannot read `operator_json` or any provider column.

## 7. Integration
1. **Workflow:** `setup` calls `stage.propose_and_gate`; the budget node calls `gate` and `priority`; `finalize` calls `stage.close_run` (report, then `SettlementPort`), exactly once (K12). The graph state carries identifiers only, and the spend counter keeps its reducer (known defect 1) and is never reset on resume.
2. **Connectors:** `call_operation` consults `gate` first, honours `prepulled` before a paid call (D-31), and passes cost, credits and category into the ledger row. Fallback on exhaustion follows the D-31 flag.
3. **Tracker:** unfunded and exhausted rows become Blocked with the reason and proof (queries run, tool, result count of zero spent) through `TrackerPort`.
4. **API:** thin router; the tenant comes only from the token; the operator report needs the operator role; approval endpoints call `approval.py` and nothing else.
5. **Jobs:** `spend_invariants` is an entry point for a nightly Cloud Run job started by Cloud Scheduler (the GCP target in the master prompt; no AWS service names). Metrics by outcome only, no tenant label, no provider names; OTel spans per gate decision and per report.
6. **Comments:** every module opens with a docstring (purpose, guarantees, failure modes) and every guarantee is tagged `GUARANTEE(<test name>)`, per the comment standard in the master audit prompt.

## 8. Anti-hallucination for any model use
This package uses **no model**. A narrative line in the operator report is a template filled from rollups. A test greps the package for LLM client imports and fails if any appear.

## 9. Test harness (build before the code)
Location `tests/spend/`, real Postgres, injected `Clock`, fakes from `tests/fakes/`, fixtures labelled `synthetic`.
1. **Scripted provider fake:** known and unknown costs, free calls, a free call that returns a cost, actual above the upper bound, negative, NaN and huge costs, a cost in the wrong unit, 429 and 5xx, credits exhausted, duplicates, slow responses.
2. **Hand-written oracles:** three synthetic runs (within cap, cap reached with partial results, extension approved) whose spend report is computed by hand and compared field by field.
3. **Property tests (hypothesis):** money never uses float, parts sum to the total, rounding is monotone and never rounds down a cost; for any call sequence `spent <= cap + overshoot`; `priority.py` is deterministic, never funds a lower row before a higher one it could afford, and never exceeds the remaining amount; the report is a pure function of its rollups; approval binding rejects any mutated field.
4. **Concurrency:** 200 parallel paid calls on one run never exceed the cap; two workers on one run; ten parallel runs of one tenant and of two tenants; approval and extension races (double approve, approve versus expire); racing finalisation yields one report and one settlement.
5. **Kill and resume** (subprocess): kill between reserve and finalise, mid-wave, during approval, and during the report write; resume; assert no under-count, a reconciled or flagged intent, one settlement, no half-written report.
6. **Lying input, every K-rule:** a call with no cap and with an unapproved cap (direct to the connector); an approval with a stale hash, another tenant's principal, an expired or replayed one, and one forged by an agent path; an unknown-cost call; a provider reporting zero for a paid call; a replay billed twice; mismatched counters; a lowered cap below spend; an extension without approval or lower than the cap; a client file and a tenant view containing a provider name, code or credit count; the operator report placed in the zip. Each ends in its typed error or documented handling, never a silent pass.
7. **Within-cap behaviour:** a run inside its cap emits zero approval requests after the first.
8. **Isolation suite extension:** two tenants never see each other's caps, snapshots, reports or prepulled files; the tenant role cannot read `operator_json`.
9. **Database tests:** insert-only tables refuse UPDATE and DELETE; the CHECK holds; the migration runs twice cleanly; the lint passes; the expand-only rule is not broken.
10. **Fault injection:** a database failure or timeout at each step of propose, approve, gate, finalise and report leaves a consistent state and a typed error.
11. **Scale smoke:** 10,000 ledger rows report within a memory cap; measure, do not assert numbers I did not give.
12. **Mutation check:** run `mutmut` (or the project's tool) on `money.py`, `gate.py`, `priority.py`, `approval.py`, `report.py`, `reconcile.py`; list surviving mutants or say it could not run.
Coverage: at least 90% on `src/spend/`, **100% on `money`, `gate`, `priority`, `approval`, `report`, `reconcile`** (the skill demands 100% on billing).

## 10. Development loop (bounded)
For each component repeat at most **5 iterations**: write or extend failing tests; smallest implementation; run its tests, `ruff`, `mypy --strict src/spend` and the isolation suite; if red, change the **code**. After 5 red iterations on one component, STOP and report the failing test, its output, what you tried and your hypothesis. Do not weaken the test, mock the unit under test, skip it, or mark it `xfail` (except for a defect already recorded in `references/13` or an ADR).
Build order: `config`, `errors`, `money`, `models`, `estimator`, `priority`, `ports` and fakes, scripted provider and oracles, migration, `balances`, `proposal`, `approval`, `gate`, `rollup`, `reconcile`, `report`, `extension`, `prepulled`, `stage`, router, nightly job, docs.

## 11. Production requirements
Type hints and `mypy --strict`; no bare `except`; no global mutable state; no clock or randomness in pure modules; typed errors only; config validated and hashed at startup; structured logs with the run id as trace id and no money figures at info level; idempotent migration; every public function documented with its guarantee; every state change a single conditional `UPDATE ... WHERE status = <expected>` or an insert, so duplicate delivery is harmless.

## 12. Gates before you finish
Run and report real results: skill self-test; `validate_assets.py`; `lint_migrations.py`; `check_rls_catalog.sql` (no rows); `ruff check`; `mypy --strict src/spend`; isolation suite; `pytest tests/spend` with coverage; the full `pytest -m "not eval"`; `scripts/scan_client_output.py` over the tenant view fixtures. Confirm: every config entry has a `source` or `status`; every K-rule has a passing lying test; no vendor name in `src/spend/` or tenant output; the docs are current.

## 13. Deliverables
`src/spend/`, the router, the migration, `config/spend.yaml`, the connector and workflow adapters, `docs/spend/` (money model, approval flow, report fields, runbook for a stuck approval and for a reconciliation failure), ADRs (table naming, cap unit, beyond-cap policy, advisory breakdown, visibility, exhaustion path), the updated open-decision register (D-27 to D-31, the new conflicts), tests and fixtures, updated `BUILD_LOG.md` and `docs/STATUS.md`.

## 14. Stop rules
Stop and ask me if: no provider cost table with sources can be found (build the rest, list exactly what to supply, V7); a change would drop or alter an existing column; D-27 blocks the cap unit without a usable default; the skill and another source conflict outside the register; you want to change the retry owner or the connector's atomic reserve; real credentials, spend or tenant data would be needed; a gate stays red after 5 iterations.

## 15. Final reply (short)
A table of components (proposal, approval, gate, priority, extension, reconciliation, report, tenant view, pre-pulled files, nightly job: Done, Partial, Blocked, and what each depends on); gates run with results; what is **proven by tests** versus assumed (state precisely: the cap is never exceeded under concurrency, no paid call without an approved cap, unknown cost never zero, integer money, derived spend reconciles, replays cost nothing, one settlement and one report per terminal run, no leak in client output); what is **not proven** (real provider prices and credit rates, balance endpoints per provider, real concurrency on the target database, the operator console flow); the decisions I must make (D-27 to D-31); what I must supply (provider cost table, credit rates, approver roles, timeouts, headroom and limits); follow-ups (tenant daily cap, monthly refresh budgets, wallet pricing and top-ups, operator console screens); open `[VERIFY]` items; commits; the next work package.
