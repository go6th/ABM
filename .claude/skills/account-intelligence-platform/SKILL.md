---
name: account-intelligence-platform
description: Build, extend or review the Account Intelligence platform (6th): a multi-tenant SaaS that researches a sales account and returns a sourced, graded brief, people list and alerts. Use for any task on its research graph, evidence grading, tenant isolation (Postgres RLS), run durability, wallet and billing, scheduled refresh and alerts, deliverables and quality gate, or the Phase B private-context vault. Follows the PRD, SaaS design and Architecture v2.
---

# Account Intelligence platform: implementation skill

You are building a production multi-tenant platform. One company in; a sourced, graded account brief, a deduplicated senior people list, and ongoing alerts out. **Isolation, durability and honest grading matter more than speed.** This skill tells you what to read, what must never be broken, how to work, and how to prove each piece is done.

Everything here comes from three source documents: the **PRD** (research process), the **SAAS** design (product behaviour) and **ARCH v2** (how to build). `references/00-sources-and-conflicts.md` says which wins when they disagree. Do not rely on memory of these documents; read the references.

## 1. Orient before you do anything
1. Read `BUILD_LOG.md` in the repo root if it exists (what is built, what is open). If it does not, copy `assets/BUILD_LOG.template.md` to the repo root and start it.
2. Work out the current phase and work package from `references/11-build-plan.md`. If the person named one ("build WP 1.2", "do the data layer"), use that.
3. Read the references the task needs (table below). Read `00` and `01` once per session; read the rest on demand.
4. Run `bash scripts/selftest.sh` once. If it fails, the skill's own tools are broken: say so before building.

| If the task touches | Read |
| --- | --- |
| Anything (once per session) | `references/00-sources-and-conflicts.md`, `references/01-product-and-process.md` |
| Overall design, stack, services | `references/02-architecture.md` |
| Tenant isolation, RLS, auth, security, injection, compliance | `references/03-tenancy-isolation-security.md` |
| Run states, graph nodes, durability, budgets, model tiers, caching | `references/04-run-lifecycle-and-agents.md` |
| Tables, migrations, vectors, retention, source codes | `references/05-data-model.md` |
| Grades, confidence, facts, coverage rows | `references/06-evidence-and-grading.md` |
| Refresh cadence, deltas, alerts | `references/07-refresh-and-alerts.md` |
| Endpoints, roles, wallet, billing | `references/08-api-auth-billing.md` |
| Internal documents, redaction, two-output rule, Phase B | `references/09-private-context-phase-b.md` |
| Tests, evals, CI/CD, infra, observability, cost | `references/10-testing-evals-cicd-ops-cost.md` |
| What to build next, in what order, with what exit test | `references/11-build-plan.md` |
| Anything unresolved, vendor facts, risks | `references/12-open-decisions-and-verification.md` |
| Using or porting the architecture document's code | `references/13-starter-code-and-known-defects.md` and `assets/starter/README.md` |

## 2. Rules that must never be broken
Each is a requirement from the sources. If a task seems to require breaking one, **stop and ask the person**; do not work around it.
1. **Isolation is enforced by the database, not by application code.** `tenant_id NOT NULL` and `ENABLE` plus `FORCE ROW LEVEL SECURITY` with `USING` and `WITH CHECK` on every tenant table **from the first migration**. Runtime role `app_rw` is not the owner and has no BYPASSRLS. Tenant context is set only by `tenant_txn()` with a transaction-local `set_config`. **Never ship a single-tenant schema "for now".**
2. **Tenant identity comes only from the verified token.** No endpoint, tool or agent accepts a tenant id from a request body, path, query or model output. Agents get **no SQL, shell or arbitrary-URL tools**.
3. **Untrusted text is read only by the tool-less extractor** that emits schema-validated JSON. Nothing that reads untrusted text can send, write or fetch. Typed tool arguments only.
4. **No fact without a source, lineage, and a grade computed by the formula.** Claims without evidence are dropped. Normalise in code, not in the model. Only **Confirmed** (read in a primary source this run) may be stated to a prospect. Absence of evidence is a **Hypothesis**, never a conclusion. A keyword match or a date range is a **lead**. A Lead needs three differently worded queries across two tools.
5. **Two outputs.** Client-facing output uses public data only. Internal material appears there only in neutral form ("Client", call notes with a date). The internal view is called **Internal Only**, never named after a person, and is never in the shared folder, the workspace zip, the public cache or another tenant.
6. **Client-facing output never names a data broker, a credit count, a call recording, a client's staff or another account.** Attribution is "6th agents". Brokers are named only in the sub-processor list of the data processing agreement. Provider names stay in internal tables and use codes (`p_search`...). Run `scripts/scan_client_output.py` on every export.
7. **Money is a ledger.** Reserve credits atomically with the run row, commit and refund at the terminal state, one settlement per run, insert-only ledgers, no negative balance. Each paid call is recorded once per `(run_id, args_hash)`. A run never exceeds its budget cap; hitting it ends in BUDGET_EXCEEDED with partial results, not an empty failure.
8. **Durability:** the LangGraph checkpoint is the source of truth. Inngest only triggers, schedules and loops time slices. One retry owner per failure class. Events carry identifiers only, never content.
9. **Never send outreach. Never delete or overwrite a client's records** (add new rows and files; superseded facts are marked, never removed). Sensitive findings (breach, layoff, lawsuit) are preparation, never openers.
10. **No silent omission.** A source that failed makes its coverage row **Blocked with a reason**, and the run continues. Every coverage row ends Done, Partial, Not done, Blocked or None with proof. Every count names its base.
11. **Pull once.** Each broker feed is pulled once per run and saved. Respect the adapter rules in `assets/routing.yaml`.
12. **Do not present assumptions as facts.** The PRD's time and value figures are working assumptions. ARCH timing and cost numbers are targets to prove. Anything tagged [VERIFY] is unverified until you test it or read the vendor's own documentation.

## 3. Precedence and conflicts
ARCH wins for how to build; the PRD for what the research must do; SAAS for what the client sees. The settled defaults (C1 to C10) are in `references/00`. If you hit a disagreement not in that register that changes a build decision, **ask**; do not choose silently. Two grade vocabularies are kept (product: Confirmed/Probable/Hypothesis/Lead; engine: A to D plus Conflicting and Blocked). **The mapping between them is open decision D-01**; use the proposal in `assets/grading.yaml` only as a working default and say so.

## 4. The work loop (one work package at a time)
1. **State the plan** in 5 lines: the work package, files you will touch, the acceptance tests, open decisions (D-xx) it depends on, [VERIFY] items it depends on. If a blocking decision is open, ask the person now, with a recommended default.
2. **Write the tests first** for the invariants involved (isolation, ledger, grading, leakage, injection). Reuse `assets/starter/tests/` and `scripts/grading_reference.py`; do not weaken an existing test to make code pass.
3. **Build the smallest increment** that satisfies the work package's "done when" line in `references/11`. No features from later phases.
4. **Run the gates** (section 5). A work package is not done until they pass. Paste the command and its summary line into the log.
5. **Log** in `BUILD_LOG.md`: what was built, tests and results, conflicts met, decisions touched, [VERIFY] items resolved with evidence.
6. **Report briefly**: what works, what the tests showed, what is next, anything that needs the person. At a phase's exit criteria, stop and report; do not start the next phase.

When you find the sources wrong, ambiguous or incomplete (it will happen; `references/13` lists ten cases), **say so, fix it in your code, add a test that would have caught it, and note it in the log.** Do not silently follow a defective listing.

## 5. Gates and commands
| Gate | Command | Passes when |
| --- | --- | --- |
| Skill assets valid | `python scripts/validate_assets.py` | prints `OK: 33 coverage rows, 5 product grades, 8 routes, 10 alert rules` |
| Migrations safe | `python scripts/lint_migrations.py migrations` | `OK` (all files linted together; see its docstring for limits) |
| Catalog RLS check | `psql "$ADMIN_DSN" -tA -f scripts/check_rls_catalog.sql` after migrating | no rows |
| Isolation suite (**hard gate**) | `pytest tests/isolation -x` | all pass; zero tolerance |
| Grading maths | `python scripts/grading_reference.py --selftest` and your own property tests | all `ok`; 100% coverage on grading and billing |
| Client-output leak scan | `python scripts/scan_client_output.py <files or unzipped package> [--extra terms.txt]` | exit 0 (checks inside docx, xlsx, pptx, zip, names and metadata) |
| Unit, integration | `pytest -m "not eval" --cov=src --cov-fail-under=80` | pass |
| Eval gate (main) | golden set, adversarial corpus | gates in `references/10` met; any injection or leakage failure blocks |
| Skill self-test | `bash scripts/selftest.sh` | `selftest OK` |

If a command cannot run in this environment (no database, no network), **say that plainly and do not claim the gate passed.** Never write "tests pass" without having run them.

## 6. Building blocks you already have
- `assets/checklist.yaml`: the 33 coverage rows (key, pass, priority, label, cadence) that seed every run.
- `assets/grading.yaml`: product grades, engine tiers, half-lives, noisy-OR rule, eval gates, the D-01 *proposal*.
- `assets/routing.yaml`: provider codes, eight routes with fallbacks, adapter rules, tool categories.
- `assets/alert_rules.yaml`: the ten merged alert rules, dedupe key, refresh probe config.
- `assets/client_output_denylist.txt`: patterns the scanner refuses in client-facing files.
- `assets/starter/`: ARCH chapter 18 code, extracted, fixed and tested (20 passing, 2 documented expected failures on PostgreSQL 16 with pgvector 0.8).
- `scripts/`: validators, migration lint, catalog check, leak scanner, grading reference, self-test.

## 7. What to decide and what to ask
**Decide yourself** (and log): file and module names inside the layout in `references/02`, library choices that follow the ARCH stack, test structure, internal refactors, migration numbering, wording of error messages.
**Ask the person first:** any open decision D-01 to D-11 that blocks the work package; anything that would change a rule in section 2; spending real money on brokers or LLMs beyond a test cap; choosing a different stack from ARCH; anything that touches real tenant data; legal, privacy or licensing questions (D-06, D-10, V7, V10: **you are not a lawyer; flag, do not decide**); a conflict between sources outside the register.
When you ask, give the question, your recommended default, and what it blocks. One round of questions, not many.

## 8. Handling [VERIFY] items
ARCH Appendix B lists V1 to V11 (model IDs and prices, cache lifetimes, Aurora regions, pgvector features, Inngest limits, vendor retention terms, and so on). Resolve one when its work package needs it: test it, or read the vendor's first-party documentation, and record the result with the evidence in the log ("verified by running X: Y" or "not verified"). **Never state a vendor limit, price or model ID as settled from memory.** Model IDs live in configuration, never in code.

## 9. Style of the code and the files you produce
Python 3.12, type hints, small modules following the layout in `references/02`. Pure functions for grading and normalisation. SQL migrations through Alembic with expand-then-contract. Structured JSON logs with a field allow-list; never log prompts, document text or tokens. Every deliverable follows the PRD format rules (Word for documents, US Letter, no em dashes, no hyphens or dashes as punctuation, plain English headings, abbreviations written out on first use) and the PRD quality gate in `references/01`.

## 10. Common mistakes to avoid
- Building the self-serve product before the concierge version, or Phase B before isolation has run in production.
- Adding `tenant_id` later. Adding RLS without `FORCE`. Writing a policy with no `TO` role clause beside a `public_reader` policy (private rows leak; defect 2).
- Parallel graph branches writing a state key with no reducer (crashes; defect 1).
- Calling the paid-call ledger "exactly once" without a per-run lease and tests for a kill between the provider response and the ledger insert, and for two workers on one run (defect 3).
- Mixing credits and dollars in one field (defect 4).
- Putting tenant id on every metric (cardinality) or prompts in logs.
- Hard-coding model IDs, prices, or half-lives.
- Letting a model compute a confidence, a fingerprint, or a normalised value.
- Treating the ARCH cost and timing numbers as measured.
- Declaring a work package done without running its tests.

## 11. Skill folder map
```
SKILL.md                         this file
assets/                          yaml data, denylist, starter code and tests, BUILD_LOG template
references/00..13                the knowledge, one topic per file
scripts/                         validators, lint, catalog check, scanner, grading reference, selftest
```
The project README (outside this folder) explains the product and workflow for humans; this skill is for the agent doing the build.
