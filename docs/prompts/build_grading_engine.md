# Prompt: build the Grading engine (schema-enforced grade, URL and supporting sentence; Confirmed and Lead rules in code), production grade

Paste everything below the line into Claude Code, opened in the project repo (built with the `account-intelligence-platform` skill).

---

Build the **Grading engine**. The schema enforces a **grade, a URL and a supporting sentence** on every claim. The rules live **in code**: **Confirmed only if the claim was read in a primary source this run; a Lead needs three differently worded queries across two tool categories before it stays a Lead.** Write production-grade code and prove it with a real harness. Never invent facts about the process, the sources or the account, and never let a model decide a grade.

## 0. Ground rules

1. Read `.claude/skills/account-intelligence-platform/SKILL.md` first and obey it. Then read `references/06-evidence-and-grading.md` (the main spec), `references/01-product-and-process.md`, `references/04-run-lifecycle-and-agents.md`, `references/05-data-model.md`, `references/12-open-decisions-and-verification.md`, `references/13-starter-code-and-known-defects.md`, `assets/grading.yaml`, `assets/routing.yaml`, `assets/checklist.yaml`, `scripts/grading_reference.py`, `assets/client_output_denylist.txt`, `BUILD_LOG.md`, `docs/STATUS.md`. If `docs/sources/` holds the PRD, read section 3.4 and Appendix B and cite them. Follow the skill's work loop: plan in five lines, tests first, smallest increment, run the gates, log.
2. **Fixed by the skill:** two vocabularies, both stored (product grade Confirmed, Probable, Hypothesis, Lead, Internal; engine grade A to D plus Conflicting and Blocked); the engine is **pure** (no I/O, no model calls, no clock); grades are computed, a model only proposes values and evidence; the database enforces isolation (`tenant_id NOT NULL`, forced RLS, `TO app_rw`, `tenant_txn`); facts are append-only with `superseded_at`; no fact without evidence; client-facing output never names brokers.
3. The mapping between the two vocabularies is **open decision D-01**: `assets/grading.yaml` holds a *proposal*. Implement it as a labelled, configurable default. Do not present it as settled.
4. No real provider calls, no real LLM calls, no real spend, no real tenant data. Fakes and recorded fixtures only. The judge and the generator are ports with scripted fakes.
5. Never claim a gate passed that you did not run. Never weaken or delete a test to pass. Commit on branch `feat/grading-engine` in small Conventional Commits. Do not push.

## 1. Preflight (before writing code)

Print a table of what exists: `src/agents/grading.py`, normalisation and fingerprint code, the source registry and lineage mapping, the facts and `fact_evidence` write path and their exact columns, payload store text access, `src/workflow/evidence_gate.py`, `src/tracker/leads.py` or any "differently worded" detector, the judge node, `tenant_txn`, the isolation suite.
- Missing dependency: define a typed `Protocol` in `src/grading/ports.py` and an in-memory fake in `tests/fakes/`. Ports: `SourceTextPort` (stored text of a source for this run), `FetchLedgerPort` (how and when a source was obtained in this run), `SourceRegistryPort`, `SupportJudgePort`, `Clock`. Production adapters that do not exist raise `NotConfigured`; the claim is then **not** graded A, B or Confirmed (see G12).
- **One implementation per rule.** If the Workflow v1 evidence gate or the tracker already contains quote, URL or Lead-rule logic, move it into `src/grading/` and leave a thin re-export there. If they do not exist yet, build here and list the integration as a follow-up.
- `src/agents/grading.py` **stays the public import path** (the skill and Workflow v1 name it). Make it a facade that re-exports the engine API from `src/grading/`.
- If `facts` or `fact_evidence` exist, **extend them expand-only** (add columns, constraints, indexes; never drop or alter an existing column; ask first if unavoidable).

## 2. Anti-hallucination protocol (build time)

1. Tier weights, half-lives, grade thresholds, the Lead rule numbers (`min_distinct_queries: 3`, `min_distinct_tool_categories: 2`) and eval gates come only from `assets/grading.yaml`. No constant in code. A test reads the file and asserts the loaded values equal it.
2. `scripts/grading_reference.py` is a behavioural spec, not scripture. Port it, run `python scripts/grading_reference.py --selftest` first and keep every worked example (c = 0.975 grade A; one upstream group c = 0.40 grade D; single fresh T1 c = 0.90 grade B; two fresh T3 groups c = 0.91 grade B). **Then fix the weaknesses I verified in it** (each needs a failing test first):
   - F1 **Non-deterministic tie-break:** the same evidence in a different order returns a different "best" value. Sort and break ties by confidence, then lineage count, then normalised value.
   - F2 **Untyped failures:** an unknown tier or attribute class raises `KeyError`, a naive datetime raises `TypeError`. Raise typed errors (`UnknownTier`, `UnknownAttributeClass`, `NaiveDatetime`); never default silently.
   - F3 **Age is measured from `retrieved_at`:** a three-year-old article fetched today scores as fresh (single T3 funding source: c = 0.70, grade B). Carry an evidence date with its kind (`observed` for a live page read, `published` for a dated document, `unknown`). See the dating proposal below.
   - F4 **Float boundaries:** compare against 0.85, 0.70 and 0.50 with a documented tolerance (or `Decimal`), and test the exact boundaries (fresh T2 plus fresh T4 is exactly 0.85 and must be grade A).
   - F5 **`now` defaults to the wall clock:** require it as an argument.
3. **Proposals of mine, not facts from the sources** (put each in config marked `status: proposal`, record an ADR, and add to the open-decision register at the next free numbers; the tracker prompt used D-12 and D-13, so start at **D-14** unless the register shows otherwise):
   - **D-14 evidence dating:** live-state pages (company site, careers page) are dated by retrieval; dated documents (articles, filings, releases) by their published date; a dated document with no published date is `unknown`, uses retrieval time for age, is flagged `date_unknown` and **cannot support grade A**.
   - **D-15 source registry:** tier by source domain and by provider code (never provider name), `is_aggregator`, upstream lineage group. T1 for the run's own account domain and verified careers host is derived from run input; every other domain is configuration marked `source: TODO, needs product owner`; an unlisted domain defaults to **T4 and is flagged** `unclassified_source`. Do not guess filing or news domains from memory.
   - **D-01 extension:** precedence of the product grade (5.3), the product grade of Conflicting and Blocked, and the judge-unavailable cap (G12).
   - **D-11 extension:** a lineage group of unknown provenance counts toward confidence but, if it is what lets the claim reach A, the claim is capped at B and flagged `needs_lineage_review`.
   - The similarity metric and threshold for "differently worded" and the stopword list: `source: TODO, needs product owner`; ship a labelled proposal that passes the synthetic corpus in section 9.
4. Do not invent lineage groups, title aliases, normalisation tables or stopword lists from memory. A kind without a configured normaliser is **opaque**: it never produces an automatic Conflicting, only a `possible_conflict` review flag.
5. No vendor or broker name anywhere in `src/grading/`, config keys, reason texts or output. Provider identity is a `provider_code` in the internal ledger only.
6. Before using any PostgreSQL feature (CHECK on existing data, deferred constraint trigger, partial index), run it on the project's test database and read the real behaviour. Do not report SQL from memory as working.

## 3. Anti-hallucination protocol (run time): the GradeGuard

Code rules, each with at least one "lying generator" test (section 9.4).

| # | Rule |
| --- | --- |
| G1 | **A model cannot set a grade.** The LLM output schema `ProposedClaim` has no grade, confidence, tier, lineage or read-flag field, and `extra='forbid'` rejects any attempt to send one. |
| G2 | **Schema enforces grade, URL and supporting sentence.** `claim_kind` is a discriminated union: **sourced** (at least one evidence item, each with `url` and `supporting_sentence`), **synthesis** (the system's own inference: `basis_claim_ids` that resolve to sourced claims, a confirming question), **absence** (no source: references the tracker proof of the queries run and a confirming question). A graded claim of any kind carries `product_grade`; a synthesis claim inherits the URLs and sentences of its basis. No claim reaches storage without all three traceable. |
| G3 | **URL check.** Normalised URL must be among this run's stored sources, scheme http or https, no credentials, host equal to the registered source domain. A URL no connector returned is dropped (`url_not_in_run`). |
| G4 | **Supporting-sentence check (deterministic).** The sentence must appear **verbatim and contiguous** (after NFKC and whitespace normalisation) in the stored text of the cited source; length within configured bounds; the claim's normalised value (names, numbers with units, platform terms) must appear in the sentence. A paraphrase, a sentence from another source or an altered figure is dropped (`unsupported_sentence`). |
| G5 | **Confirmed only if read in a primary source this run.** `read_in_primary_this_run` is computed by code from the fetch ledger and is true only when all hold: origin is a **full page read** (not a search snippet, feed item, broker record or cached copy); the source tier is T1; the fetch belongs to **this run** (run id matches and it happened after run start); the supporting sentence was verified in that fetched text (G4); the claim is not Conflicting. No caller parameter can set it. |
| G6 | **Product grade is derived by one pure function** with fixed precedence (5.3). Only Confirmed may be stated to a prospect; Probable, Hypothesis, Lead and Internal carry `may_state_to_prospect = false` and render as questions. |
| G7 | **Absence of evidence is Hypothesis, never a conclusion.** A keyword match is a Lead. A date range in a detection feed is a Lead. |
| G8 | A Hypothesis without a confirming question fails validation. A Probable without stated reasoning fails validation. Internal never becomes Confirmed from internal material alone unless the client marked the source authoritative (Phase B flag, default false). |
| G9 | **Lead rule in code (5.4).** A Lead stays a Lead only with **three pairwise differently worded queries across at least two tool categories**, every one a successful call in this run's ledger. Repeats and near-duplicates are rejected, not counted. Fewer than that means the Lead is `pending` and cannot be final. |
| G10 | **Deterministic engine.** Same evidence in any order gives byte-identical output. `now` is injected and timezone-aware. Unknown tier or class raises a typed error. |
| G11 | **Lineage.** Sources sharing an upstream are one group (max within a group, never the sum). Unknown provenance is its own group **and flagged**; independence is never assumed silently (D-11 extension). Aggregator sources are labelled. |
| G12 | **Judge pass.** For every claim graded A or B, and every Confirmed, a judge that is **not** the generator model tier checks that the cited sentence supports the claim. The judge sees only the claim and the sentence, returns schema-validated output (one bounded re-ask, then treat as unavailable). Unsupported means downgrade or drop and log it. **Judge unavailable or `NotConfigured`: the claim is not granted A, B or Confirmed; cap at engine C and Probable, flag `unjudged`.** Never skip silently. |
| G13 | **Conflicts.** Two independent groups asserting different normalised values, both c above 0.50, make the attribute Conflicting: show both with evidence, neither is Confirmed (D-01 extension), each carries a question. |
| G14 | **Quarantine and sanitisation.** Strings are length-capped and stripped of control characters; a canary or an instruction echoed in a supporting sentence drops the claim and flags the source hostile. The engine never reads raw page text except through `SourceTextPort` for G4. |
| G15 | **One write path.** `record_graded_fact()` inserts the fact and its evidence rows in one `tenant_txn`; the database mirrors the key rules (section 6). A later downgrade writes a **new** superseding fact row and a grade event, never an UPDATE of the value. |

## 4. Architecture to build

```
src/grading/
  config.py     loads and validates assets/grading.yaml and config/source_registry.yaml; content hashes recorded with the run
  errors.py     GradingError > UnknownTier, UnknownAttributeClass, NaiveDatetime, SchemaViolation, UrlNotInRun, SpanNotFound, NotPrimaryRead, LeadRuleViolation, JudgeUnavailable, ConfigError
  schema.py     ProposedClaim (LLM output), GradedClaim, evidence models; JSON Schema exported to docs/grading/schema.json
  engine.py     pure: recency, group_strengths, confidence, engine_grade, grade_attribute
  product.py    pure: product_grade() precedence, statement_policy(), display_label()
  primary.py    pure: read_in_primary_this_run(evidence, fetch_records, run) (G5)
  spans.py      verify_url, verify_span, value_support, sanitise, canary check (G3, G4, G14)
  leads.py      LeadVerification state, distinct_queries(), submit_query(), evaluate_lead() (G9)
  lineage.py    registry lookup; unknown becomes own group plus flag (G11)
  judge.py      SupportJudgePort, apply_support_verdict() (pure), judge-differs-from-generator check (G12)
  pipeline.py   grade_claims(): guard, engine, product, judge, then persist; returns GradedClaim or Dropped(reason)
  repo.py       record_graded_fact() through tenant_txn (G15)
  ports.py      Protocols
src/agents/grading.py        public facade (re-exports)
config/source_registry.yaml  tiers, aggregator flags, lineage groups (TODO entries marked)
docs/grading/{schema.json,rules.md}   generated; a test fails if stale
migrations/                  one expand-only revision
evals/grading_calibration.py offline calibration harness (section 9.9)
```
Purity rule: `engine.py`, `product.py`, `primary.py`, `leads.py`, `judge.apply_support_verdict` do no I/O and read no clock or environment.

## 5. The rules in detail

### 5.1 Schemas
`ProposedClaim` (model output): `claim_kind`, `attribute_key` (dotted, e.g. `exec.cto.name`), `value`, `evidence[{url, supporting_sentence}]` (sourced), `basis_claim_ids` (synthesis), `confirming_question` (synthesis, absence), `reasoning` (length-capped). `GradedClaim` (built only by `pipeline.grade_claims`): adds `product_grade`, `engine_grade`, `confidence`, `read_in_primary_this_run`, per-evidence `source_code, url, tier, origin, lineage_group, is_aggregator, supporting_sentence, span_sha256, date_kind, evidence_date`, `flags`, `lead` (verification record), `schema_version`, `config_hash`. Both are strict, frozen, `extra='forbid'`; cross-field validators enforce G2 and G8 whoever constructs them. Fixtures are validated against the exported JSON Schema.

### 5.2 Engine
Port the reference logic with fixes F1 to F5. Config-driven weights and half-lives, `r = 0.5 ** (age_days / half_life)`, max within a lineage group, noisy-OR `c = 1 - prod(1 - w*r)`, grades A (`c >= 0.85`, at least two independent groups, one T1 or T2; keep the reference's "tier of the group's strongest contributor" and record that reading in the ADR), B, C, D, Conflicting, Blocked (a required source failed or was rate limited after retries; show the reason). Never output a grade for an empty evidence list: return `no_evidence` and let the caller record the Hypothesis.

### 5.3 Product grade precedence (D-01 proposal; implement as ordered config)
1. No evidence: **Hypothesis** (absence), question required.
2. Conflicting: no value above **Probable**, flagged, each with a question.
3. Support only from client internal material: **Internal**.
4. `read_in_primary_this_run` true (G5): **Confirmed**; the engine grade is stored alongside and does not grant it.
5. Engine A or B without a primary read: **Probable**, reasoning required.
6. Engine C, or a synthesis: **Hypothesis**, question required.
7. Engine D, or support only from a secondary or aggregator source: **Lead** (aggregators labelled), subject to G9.
Caps from G12, D-11 and D-14 are applied after this step and recorded in `flags`.

### 5.4 Lead verification (G9)
State per Lead: `pending` (needs queries), `complete` (three accepted queries, two categories, no source upgraded it: stays Lead), `upgraded` (a verification call produced a supporting source; the claim is **re-graded through the normal path**, never edited by hand), `incomplete(reason)` (budget, source blocked, or no distinct query possible; shown labelled "Lead, verification incomplete", never as a plain Lead). `submit_query(state, query, tool_category, call_ref)` accepts a query only if: the call is a successful call in this run's ledger (and its category matches the ledger, not the caller); the normalised token set (config stopwords, lowercase, NFKC, punctuation stripped) has similarity **below the configured threshold against every earlier accepted query** for the same Lead; the category is in `routing.yaml` `tool_categories`. Reordered words, case or punctuation changes, or adding the account name do not make a query different. The Lead is complete only when three accepted queries span at least two categories. A parked Lead gets **one** re-pass when later evidence supports it. `evaluate_lead()` is the single function the tracker row `lead_verification` and the workflow verification stage call.

## 6. Database (one expand-only revision; follow `scripts/lint_migrations.py` rules R1 to R5 including `lint:covers`)
1. `facts` constraints (add as `NOT VALID`, then `VALIDATE` only if existing rows comply; if they do not, stop and ask): `product_grade IS NOT NULL`; **`product_grade <> 'confirmed' OR read_in_primary_this_run`**; `product_grade IN ('hypothesis','lead','probable','confirmed','internal')` stays as is.
2. `fact_evidence` new columns: `supporting_sentence NOT NULL` with a length CHECK, `span_sha256`, `origin`, `source_tier`, `date_kind`, `evidence_date`, `evidence_kind` (`source`, `inherited`, `search_proof`). Absence hypotheses are the one case without a source: check how the current `fact_evidence` handles `source_id`; **if it is NOT NULL and blocks `search_proof` rows, stop and ask me** (relaxing it alters an existing column).
3. Deferred constraint trigger: a fact must have at least one evidence row of any kind at commit. Verify it fires for `app_rw` under RLS.
4. New tables, all with `tenant_id NOT NULL`, forced RLS (`USING` and `WITH CHECK`, `TO app_rw`): `lead_verifications` and `lead_verification_queries` (insert-only; query text, category, `call_ref`, accepted flag, rejection reason), `grade_events` (insert-only: fact, from, to, actor, reason code), `claim_drops` (insert-only: run, reason code, attribute key, claim hash and a sanitised truncated excerpt; **drops are always counted, never silent**).
5. Extend `tests/isolation` for every new table; `scripts/check_rls_catalog.sql` must return no rows.

## 7. Loops (all bounded, recorded, testable)
- **Lead query loop:** at most 3 accepted queries per Lead, at most a configured number of **rejected** proposals per Lead (proposal: 3; then `incomplete(no_distinct_query)`), at most 1 re-pass. No unbounded "try another wording".
- **Judge re-ask:** at most 1; then unavailable (G12).
- **Per-claim isolation:** `grade_claims` processes a batch claim by claim; one claim's failure becomes a typed `Dropped(reason)` and never aborts the batch. Every claim ends graded or dropped with a counted reason.
- The grading layer adds **no provider retries**; retries belong to the node `RetryPolicy`. Poison input and schema failures are not retried.
- Every loop records its exit reason (`converged`, `cap_reached`, `no_progress`, `budget`, `deadline`) in the run state, and a property test proves termination for any generated sequence of proposals.

## 8. Integration
1. Workflow v1: `distill_grade` calls `grade_claims()`; `judge_verify` calls the judge port and `apply_support_verdict()`; the evidence gate delegates to `spans.py`.
2. Tracker: the `lead_verification` row and `claims_dropped` downgrades use `evaluate_lead()` and the drop counters; the grading layer never writes tracker rows itself.
3. Data layer: `record_graded_fact()` is the only fact write path. Deliverable builders call `statement_policy()` and `display_label()`; a Confirmed line may be stated, everything else is phrased as a question.
4. Record `config_hash` (grading and registry) with `graph_version` on the run. Metrics by grade and outcome only, no tenant label; claim text and sentences never logged at info level (canary test).

## 9. Test harness (build before the code)
Location `tests/grading/`, real Postgres for the repo tests, injected `Clock`.
1. **Reference parity:** run the reference examples and a table of hand-computed cases; random inputs compared against `scripts/grading_reference.py` must agree except where F1 to F5 intentionally differ (each difference listed in a test).
2. **Property tests (hypothesis):** c in [0, 1]; adding an independent source never lowers c; adding a source to an existing group never raises c above that group's maximum; grade monotone in c; decay monotone in age; order independence (any permutation, same output); A only with at least two groups and a T1 or T2 group; **Confirmed iff the code-computed primary read** over generated evidence; no non-Confirmed grade ever has `may_state_to_prospect` true.
3. **Boundary tests:** exactly 0.85, 0.70 and 0.50; fresh T2 plus fresh T4 is A; one half-life of age; timezone-aware only.
4. **Lying-generator tests (every G-rule), scripted FakeLlm:** sends a grade field; fabricates a URL; uses a URL from another run or tenant; paraphrases instead of quoting; quotes a sentence from a different source; alters a figure; claims a value the sentence does not contain; says "Confirmed" in free text; omits the confirming question; claims a read when only a snippet, feed item or broker record exists; reuses a fetch from a previous run (refresh); injects the canary or an instruction in the sentence; proposes three near-duplicate Lead queries (case, word order, punctuation, account name added); three queries in one category; queries missing from the ledger; counts failed calls; keeps proposing rejected queries forever; supplies a synthesis whose basis does not resolve. Each must be dropped or capped with its typed reason and a counted `claim_drops` row.
5. **Judge fakes:** supports, unsupported, invalid output (one re-ask, then unavailable), unavailable, same tier as the generator (`ConfigError`). Assert G12 caps.
6. **Lead state tests:** every transition; the three-query and two-category boundary (3 queries in 1 category, 2 queries in 2 categories, 3 in 2); upgrade re-grades through the normal path; incomplete is never rendered as a plain Lead; rejected-proposal cap terminates.
7. **Database tests** (raw SQL as `app_rw`): inserting Confirmed with `read_in_primary_this_run = false` is refused; a fact without evidence is refused at commit; event tables refuse UPDATE and DELETE; fact and evidence are atomic under SIGKILL mid-transaction; two tenants on the same domain never cross; idempotent replay of `record_graded_fact` after a kill writes no duplicate.
8. **Determinism and drift:** two runs with identical fakes give identical graded output hashes; `docs/grading/schema.json` and `rules.md` are regenerated and a test fails if stale; fixtures named and labelled `synthetic`.
9. **Calibration harness (`evals/`, marker `eval`, skipped by default):** computes precision of A and B, citation validity, calibration error and expected-attribute coverage against the gates in `assets/grading.yaml`, from a golden-set loader. With only synthetic labelled data the result is marked **Blocked: needs analyst-reviewed golden accounts**; no number is quoted as a product claim.
10. **Mutation check:** run a mutation tool (for example `mutmut`) on `engine.py`, `product.py`, `primary.py`, `leads.py` if it installs; swapping max for sum, `>=` for `>` or dropping the primary-read condition must be caught. List surviving mutants or say it could not run.
Coverage: at least 90% on `src/grading/`, **100% on `engine.py`, `product.py`, `primary.py`, `leads.py`, `spans.py` and `judge.apply_support_verdict`**.

## 10. Development loop (bounded)
For each component repeat at most **5 iterations**: write or extend failing tests; smallest implementation; run its tests, `ruff`, `mypy --strict src/grading` and the isolation suite; if red, change the **code**. After 5 red iterations on one component, STOP and report the failing test, its output, what you tried and your hypothesis. Do not weaken the test, mock the unit under test, skip it, or mark it `xfail` (except for a defect already recorded in `references/13` or an ADR).
Build order: `config`, `errors`, `engine` (reference parity, then F1 to F5), `lineage`, `product`, `schema`, `spans`, `primary`, `leads`, `judge`, `ports` and fakes, FakeLlm and fixtures, `pipeline`, migration, `repo`, facade, integration, docs generation, calibration harness, mutation check.

## 11. Production requirements
Type hints everywhere and `mypy --strict`; no bare `except`; no global mutable state; no clock or randomness in pure modules; config validated at startup and hashed; structured logs with run id as trace id; one span per `grade_claims` batch; idempotent migration that runs twice cleanly; every public function documented with its guarantees; every error typed.

## 12. Gates before you finish
Run and report real results: skill self-test; `python scripts/grading_reference.py --selftest`; `validate_assets.py`; `lint_migrations.py`; `check_rls_catalog.sql` (no rows); `ruff check`; `mypy --strict src/grading`; isolation suite; `pytest tests/grading` with coverage; full `pytest -m "not eval"`. Confirm: loaded config equals `assets/grading.yaml`; every G-rule has a passing lying-generator test; the DB refuses a Confirmed without a primary read as `app_rw`; the schema doc is current; no vendor name in `src/grading/`.

## 13. Deliverables
`src/grading/`, the facade, the migration, `config/source_registry.yaml`, `docs/grading/` (schema, rules, reference findings F1 to F5 with the failing tests that prove them), ADRs (precedence and caps, dating, source registry, Lead rule ownership, move of any existing logic), the updated open-decision register (D-14, D-15 and the D-01 and D-11 extensions), tests, fixtures, the calibration harness, updated `BUILD_LOG.md` and `docs/STATUS.md`.

## 14. Stop rules
Stop and ask me if: a change would drop or alter an existing column (including relaxing `fact_evidence.source_id`); existing fact rows violate a new CHECK; D-01, D-11, D-14 or D-15 blocks you without a usable default; the skill and another source conflict outside the register; you want to alter the architecture in section 0.2; real credentials, spend or tenant data would be needed; a gate stays red after 5 iterations.

## 15. Final reply (short)
A table of components (Done, Partial, Blocked); gates run with results; what is **proven by tests** versus assumed (state the guarantees precisely: no model-set grade, no Confirmed without a code-verified primary read this run, no Lead finalised without three distinct queries across two categories, no fact without URL and verbatim supporting sentence, deterministic output); the reference weaknesses F1 to F5 and how each was fixed; the proposals needing my decision (D-01 and D-11 extensions, D-14, D-15, similarity threshold); integration follow-ups (Workflow v1, tracker, deliverable builders); open `[VERIFY]` items; commits; the next work package.
