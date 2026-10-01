---
name: account-intelligence-builder
description: SUPERSEDED by the account-intelligence skill; use that one for all work on this repo. Kept for history (the first Phase 0 build skill).
---

# Account Intelligence builder

This skill turns the roadmap into working code without drifting from it. Follow every step in order.

## 1. Sources of truth

Read these before writing code. Quote section numbers in commits and docs.

| Document | Where | What it settles |
|---|---|---|
| Production roadmap | `~/Downloads/Account_Intelligence_Production_Roadmap.pdf` | Phases, work items, exit criteria, architecture (s4), data model (s11.1), routing (s11.2), quality (s11.3), security (s11.4), technology (s12) |
| PRD and current system | `Account_Intelligence_Use_Case_PRD_and_Current_System.docx` (repo root) | Appendix A coverage rows, Appendix B grades, deliverables (s3.4), standing rules (s3.5), the 13 step run (s4.2), routing (s4.3) |
| Service design | `Account_Intelligence_SaaS_and_Enhanced_Version.docx` (repo root) | Suggested refresh cadences (s5.1), model split guidance (s4.3) |

If the PDF and the PRD disagree, the PDF wins for engineering scope and the PRD wins for research rules. Record the conflict in `docs/PHASE<n>_STATUS.md`.

The older files `Account_Intelligence_SaaS_Architecture_v2.pdf` and `..._Detailed_Roadmap_Task1-100.pdf` drove the legacy code in `src/abm_generator/`. They are not the source of truth for new work. See `docs/AUDIT.md`.

## 2. Repository layout

```
app/                    application package (new work goes here)
  settings.py           environment-only settings, secrets as SecretStr
  rules/                pydantic schemas + loader for config/*.yaml
  golden/               golden account schema + loader
  llm/                  provider interface, fake provider, model router
  brokers/              connector interface + recorded-response doubles
  people.py             people merge, dedupe, exclusions
  benchmarks/           model split and people source harnesses
  decision_log.py       writes evidence into docs/decision_log.md
  workflows/            Temporal workflows and activities
  worker.py             Temporal worker entry point
config/                 versioned rules: checklist, grading, routing, output_spec, models, people_scope
fixtures/recorded/      recorded broker responses (synthetic until replaced)
tests/app/              tests for app/
tests/evals/golden/     golden accounts (JSON) + schema + README
infra/terraform/        dev, staging, production as code
scripts/deploy.sh       one-command deploy (make deploy-staging)
docs/                   decision log, data handling, backlog, status, audit
src/abm_generator/      LEGACY (earlier roadmap). Do not extend; port deliberately.
```

## 3. Rules that are never broken

1. **Never send.** No code path that sends outreach: no email, chat, SMS or CRM write of messages. `tests/app/test_guards.py` enforces banned imports in `app/`. Alerts (Phase 4) notify our own users only and need their own review.
2. **No live paid calls in tests or Phase 0.** Brokers and models go through interfaces; tests use `RecordedConnector` and `FakeProvider`. Live adapters arrive in Phase 1 and are off unless keys are set.
3. **Client-safe output.** Broker names, credit counts, costs, call recordings, client staff and other accounts never appear in client-facing files or views. Broker names live only in `config/routing.yaml` and internal docs marked INTERNAL.
4. **Trust model is fixed.** Four grades (Confirmed, Probable, Hypothesis, Lead). Every fact carries grade, URL and the exact sentence relied on. Only Confirmed may be stated to a prospect. Absence of evidence is Hypothesis. A Lead needs three differently worded queries across two tools before it stays a Lead.
5. **No silent omissions.** Every coverage row ends Done, Partial, Not done, Blocked or None with proof (queries, tool, result count).
6. **Never overwrite.** New rows and new files only for client and operator records.
7. **Config, not code, for rules.** Coverage rows, grades, routing, output spec and model choice live in `config/`. Every run pins the config bundle version. Change a rule by editing config and bumping its `version`.
8. **Do not invent.** No features outside the PDF. No fabricated benchmark numbers, sign-offs, company facts or legal conclusions. Synthetic data is labelled `synthetic: true` and named "Synthetic ...".
9. **Secrets from environment only.** No keys in code, config, logs or `.env` files committed. Settings never read a dotenv file.
10. **Plain English, no em dashes** in docs and config text (`tests/app/test_guards.py` checks new docs).

## 4. How to run a phase

1. Read the phase section of the PDF plus s3, s4, s11, s12. List every work item, deliverable and exit criterion.
2. Write or update `docs/PHASE<n>_STATUS.md` with one row per item, status `Not started`.
3. Build config first, then schemas and loaders, then logic, then the workflow wiring. Tests with every module.
4. Run `make app-check` (ruff, ruff format check, mypy strict, pytest) until green. Fix causes, never skip tests.
5. Check each exit criterion. Status is one of:
   - **Done**: evidence is a file path or a test name that passes now.
   - **Needs human**: a person must act (sign-off, counsel, real data, cloud account, live keys). Write the exact next step.
   - **Blocked**: cannot proceed; say on what.
6. Stop and report before the next phase. Report: done, needs human, failures, files created.

## 5. Definition of done per phase (from the PDF)

- **Phase 0**: decision log signed; CI green on every commit; staging deploy in one command; at least three golden accounts loadable by tests; checklist as versioned config; Phase 1 backlog estimated and agreed.
- **Phase 1**: three golden accounts through the API with no manual steps except the cap; at least 90 percent of High rows same or better than the manual run; zero gate failures; every graded row has a working link; spend within cap; no source pulled twice.
- **Phase 2**: ten concurrent runs without throttling failures; median turnaround under 4 hours; credit exhaustion needs no hand-supplied file; eval suite green; no unsupported Confirmed claim.
- **Phase 3**: two pilot tenants live; isolation verified by tests and external pen test; exports match on-screen rows; seller usefulness instrumented.
- **Phase 4**: 20 monitored accounts per pilot tenant; 70 percent alerts rated useful; refresh cost ratio reported; backup restore and DR drill passed.

Targets marked "to validate" in the PDF stay labelled that way.

## 6. Commands

```
make app-check          lint + format check + mypy + pytest for app/
make temporal-up        local Temporal dev server via docker compose
make worker             run the Temporal worker
make hello              start the hello-world workflow against the local server
make bench-model-split  offline model split benchmark (fake provider)
make bench-people       offline people source benchmark (recorded doubles)
make deploy-staging     one-command staging deploy (DRY_RUN=1 to plan only)
```
