# Repository audit (1 October 2026)

Scope: the whole project folder, compared with the new source of truth, the production roadmap PDF (`Account_Intelligence_Production_Roadmap.pdf`, 19 pages, dated 1 October 2026), and the PRD it is built on (`Account_Intelligence_Use_Case_PRD_and_Current_System.docx`).

## 1. What was in the repository before this work

| Area | Contents | Built against |
|---|---|---|
| `src/abm_generator/` | SQLAlchemy models, RLS session helper (`tenant_txn`), exactly-once tool ledger, resumable slice runner, structured logging | Earlier roadmap (`Account_Intelligence_SaaS_Detailed_Roadmap_Task1-100.pdf`, tasks T1 to T14) |
| `migrations/` | Alembic migration 0001: tables, ENABLE and FORCE row-level security, runtime role grants | Earlier roadmap T10 |
| `tests/unit`, `tests/isolation`, `tests/spike` | 45 unit tests (pass offline), RLS isolation suite and durability spike (need live Postgres) | Earlier roadmap |
| `docs/adr/` | 16 ADRs marked Accepted, including LangGraph checkpoints (ADR-002) and Inngest outer loop (ADR-003) | Earlier architecture v2 PDF |
| `docs/phase0/`, `docs/legal/`, `docs/reference/`, `docs/golden_set/` | Requirements, risk and conflict registers, gate review (verdict AMEND), legal briefing, DPA template, reference set, golden set protocol | Earlier roadmap |
| `spikes/` | Prompt cache ratio and broker economics dry runs | Earlier roadmap T12, T13 |
| `.github/workflows/ci.yml` | Lint, mypy, Postgres, migrations, unit, isolation, checkers; runs on pull requests and pushes to `main` only | Earlier roadmap |

## 2. Findings against the new roadmap

| # | Finding | Impact | Action taken |
|---|---|---|---|
| A1 | The repository followed a different, older roadmap. The new roadmap's Phase 0 (decision log, golden accounts, checklist as config, benchmarks, three environments, hello-world workflow) did not exist. | Phase 0 exit criteria unmet | Built in `app/`, `config/`, `infra/`, `docs/` (see `docs/PHASE0_STATUS.md`) |
| A2 | `docs/reference/coverage_tracker.csv` has 33 rows, but they are generic company attributes (legal name, HQ, employee count and so on), not the 33 coverage rows in PRD Appendix A. | Any code using it would track the wrong work | `config/checklist.yaml` now carries Appendix A exactly; tests pin counts and priorities |
| A3 | ADR-002 and ADR-003 chose LangGraph checkpoints and Inngest. The new roadmap recommends Temporal with agent steps as activities. | Conflicting orchestration decisions | Recorded as context in `docs/decision_log.md` D1; Temporal skeleton built; ADRs to be marked superseded if D1 is signed |
| A4 | Tenancy and RLS were built first. The new roadmap places tenancy in Phase 3 (weeks 19 to 27). | Not wrong, but early | Legacy code kept untouched under `src/abm_generator/` as a Phase 3 head start; not extended |
| A5 | The CI workflow ran only on `main` and pull requests; the roadmap requires CI on every commit. | Exit criterion | New `.github/workflows/app.yml` runs on every push and pull request |
| A6 | No infrastructure as code, no environments, no deploy command. | Exit criterion | Terraform for dev, staging and production plus `make deploy-staging` |
| A7 | No golden accounts; `fixtures/golden_v0/` holds only a template. | Exit criterion | Three SYNTHETIC golden accounts with a schema and loader; real ones need the analyst |
| A8 | The skill folder from the current system (Node docx builder, openpyxl workbook logic, `rowcheck.py`, `doc_checks.py`, `third_party_scan.py`, `strip_tool_names.py`) is not in the repository. | Blocks Phase 1 export builders and quality gate ports | Logged as Needs human in `docs/PHASE0_STATUS.md` and `docs/phase1_backlog.md` |
| A9 | No `account-intelligence-builder` skill existed. | Process drift | Written at `.claude/skills/account-intelligence-builder/SKILL.md` |
| A10 | Live isolation suite never ran (docker socket not available to the session user). Still true. | Legacy gate T10 stays AMEND | Unchanged; relevant again in Phase 3 |
| A11 | Local virtual environment runs Python 3.14; CI pins 3.12. | Version skew | Code written for 3.12 and checked by CI on 3.12 |

## 3. How old and new code live together

- New work goes in `app/` and is checked by `make app-check` and `.github/workflows/app.yml`.
- `src/abm_generator/` is legacy. It still builds and its unit tests still pass. Port pieces deliberately (for example the schema into Phase 1 data layer v1, RLS into Phase 3); do not extend it in place.
- The legacy `docs/` folders stay as historical working papers. Where they conflict with the roadmap, the roadmap wins (see the skill, section 1).
