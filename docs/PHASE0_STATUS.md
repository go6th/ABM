# Phase 0 status: decisions and foundations

Source: roadmap section 6. Updated 1 October 2026.

Status values: **Done** (evidence passes now), **Needs human** (a person must act; the next step is stated), **Blocked** (cannot proceed; the blocker is stated). `tests/app/test_status_doc.py` checks that every status here is one of these and that every file cited as evidence exists.

Verification run on 1 October 2026 (local, Python 3.14 venv): `make app-check` green (ruff, ruff format, mypy strict on `app`, pytest `tests/app`); legacy `tests/unit` still green; `terraform validate` green for all three environments; `DRY_RUN=1 scripts/deploy.sh staging` planned successfully.

## Work items (roadmap section 6)

| Item | Status | Evidence | Next step |
|---|---|---|---|
| Workflow engine decision | Needs human | `docs/decision_log.md` (D1: options, criteria, recommendation, Proposed); `app/workflows/hello.py` | Tech lead scores the comparison criteria; the three owners sign D1. If Temporal is signed, mark `docs/adr/ADR-002-langgraph-checkpoint.md` and `docs/adr/ADR-003-inngest-outer-loop.md` superseded. |
| Model split benchmark: harness | Done | `app/benchmarks/model_split.py`; `tests/app/test_benchmarks.py::test_offline_run_compares_both_arms` | None |
| Model split benchmark: live run | Needs human | `docs/decision_log.md` (D2 evidence slot says "No evidence yet") | Add real golden accounts; set `ANTHROPIC_API_KEY`; implement the provider adapter behind `app/llm/provider.py` (Phase 1 work, kept out of Phase 0 by the no-live-calls rule); run `python -m app.benchmarks.model_split --live --decision-log docs/decision_log.md`. Fill model prices in `config/models.yaml` so cost is reported. |
| People source decision: harness | Done | `app/benchmarks/people_source.py`; `tests/app/test_benchmarks.py::test_people_source_comparison` | None |
| People source decision: live run | Needs human | `docs/decision_log.md` (D3 evidence slot says "No evidence yet") | Agree a credit cap; with `BITSCALE_API_KEY` and `SUMBLE_API_KEY`, capture recordings for the real golden accounts into `fixtures/recorded/`; run `python -m app.benchmarks.people_source --decision-log docs/decision_log.md`. |
| Golden accounts | Needs human | `tests/evals/golden/README.md`; three SYNTHETIC files load in `tests/app/test_golden.py::test_at_least_three_golden_accounts_load` | Analyst selects three to five September runs and writes them in the schema with reviewer and date; delete the synthetic files. |
| Checklist to config | Done | `config/checklist.yaml`, `config/grading.yaml`, `config/routing.yaml`, `config/output_spec.yaml`, `config/models.yaml`, `config/people_scope.yaml`; `app/rules/loader.py`; `tests/app/test_rules.py` | Analyst confirms the shelf-life mapping in `config/checklist.yaml` (sourced from the service design section 5.1; rows with no cadence are left unset on purpose). |
| Environments and CI: repository and CI | Done | `.github/workflows/app.yml` (every push and pull request: lint, format, mypy, pytest, offline benchmarks, terraform validate, staging dry run); `Makefile` target `app-check` | Push the branch and confirm the first GitHub Actions run is green (not yet pushed from this session). |
| Environments and CI: infrastructure as code | Done | `infra/terraform/modules/environment/main.tf`; `infra/terraform/envs/dev/main.tf`, `infra/terraform/envs/staging/main.tf`, `infra/terraform/envs/production/main.tf`; `infra/README.md` (why Terraform) | None for the contract; cloud resources follow the cloud choice below. |
| Environments and CI: cloud and region choice | Needs human | `infra/terraform/envs/staging/terraform.tfvars` (`cloud_provider = "unset"`) | Product and tech lead choose cloud and region (roadmap section 16, week 2); set them in all three tfvars; add the cloud module, remote state backend and worker rollout (`infra/README.md`). |
| Environments and CI: secrets manager | Needs human | `app/settings.py` (environment only, `SecretStr`); `tests/app/test_settings.py` | Create the secrets manager entries listed in the module's `secret_names` once the cloud is chosen. |
| Data protection review | Needs human | `docs/data_handling.md` (DRAFT FOR COUNSEL REVIEW) | Product owner sends the draft to counsel on day one; counsel answers section 8. Start the broker terms review in parallel. |

## Deliverables (roadmap section 6)

| Deliverable | Status | Evidence | Next step |
|---|---|---|---|
| Decision log with the three decisions, each with evidence from the benchmarks | Needs human | `docs/decision_log.md`; `tests/app/test_decision_log.py` | Live benchmark runs (above), then sign-off. |
| Repository, CI and three environments running a hello-world workflow | Needs human | Hello-world passes in `tests/app/test_workflow.py::test_hello_workflow_end_to_end`; local stack in `docker-compose.yml` (`temporal`, `worker`) | Run `make temporal-up`, `make worker`, `make hello` on a machine with docker access (this session's user is not in the docker group). Deploy to dev, staging and production after the cloud choice. |
| Golden account set and the checklist as versioned config | Needs human | Config: `config/checklist.yaml` and `tests/app/test_rules.py::test_version_is_recorded`. Golden: synthetic only | Real golden accounts (above). |
| Written data-handling approach for people data | Needs human | `docs/data_handling.md` | Counsel answers the open items in section 8 of the draft. |

## Exit criteria (roadmap section 6)

| Exit criterion | Status | Evidence | Next step |
|---|---|---|---|
| Decision log signed off by the tech lead, the analyst owner and the product owner | Needs human | `docs/decision_log.md` (sign-off tables empty) | Sign after evidence is in. |
| CI green on every commit | Needs human | `.github/workflows/app.yml`; the same checks pass locally (`make app-check`) | Push and confirm the GitHub Actions run. |
| A deploy to staging in one command | Needs human | `scripts/deploy.sh`; `Makefile` target `deploy-staging`; `tests/app/test_infra.py`; dry run plans successfully | Cloud choice, registry and credentials (`infra/README.md`), then `make deploy-staging`. |
| At least three golden accounts stored and loadable by tests | Needs human | Mechanism done and three SYNTHETIC accounts load: `tests/app/test_golden.py::test_at_least_three_golden_accounts_load` | The criterion means real baselines: analyst adds three to five reviewed September runs. |
| Phase 1 backlog estimated in person-weeks and agreed | Needs human | `docs/phase1_backlog.md` (52 person-weeks, owners per item) | Tech lead and product owner agree or adjust the estimates and sign. |

## Build items from the Phase 0 brief

| Item | Status | Evidence |
|---|---|---|
| Monorepo layout (`app/`, `config/`, `tests/`, `docs/`, `docker-compose.yml`, `pyproject.toml`, `.env.example`) | Done | `.claude/skills/account-intelligence-builder/SKILL.md` section 2 |
| CI workflow running lint, type-check and pytest on every commit | Done | `.github/workflows/app.yml` |
| Three environments as infrastructure as code | Done | `infra/terraform/envs/production/terraform.tfvars` |
| Secrets read from environment variables only | Done | `tests/app/test_settings.py::test_dotenv_files_are_ignored` |
| One-command staging deploy script | Done | `scripts/deploy.sh` |
| Hello-world Temporal workflow with one activity, covered by a test | Done | `tests/app/test_workflow.py` |
| Rule config with pydantic loader and version string | Done | `tests/app/test_rules.py::test_invalid_configs_fail` |
| Golden account schema, loader, three synthetic samples, README | Done | `tests/evals/golden/golden_account.schema.json` |
| Model split benchmark harness with fake provider, CSV and Markdown output | Done | `tests/app/test_benchmarks.py::test_model_split_cli_writes_csv_and_markdown` |
| People source benchmark with recorded doubles | Done | `fixtures/recorded/bitscale`, `fixtures/recorded/sumble` |
| Decision log writer with template slots | Done | `app/decision_log.py` |
| No outreach code path; no network clients in `app/` | Done | `tests/app/test_guards.py` |

## Keys needed to run live (none are needed for Phase 0)

Listed in `app/settings.py` (`LIVE_KEYS`) and the Terraform module `secret_names`: `ANTHROPIC_API_KEY`, `PARALLEL_API_KEY`, `FIRECRAWL_API_KEY`, `PREDICTLEADS_API_KEY`, `PREDICTLEADS_API_TOKEN`, `BITSCALE_API_KEY`, `SUMBLE_API_KEY`, plus `DATABASE_URL`. A real deploy also needs `IMAGE_REGISTRY` and `DEPLOY_CREDENTIALS_REF`.

## Carried over from the earlier roadmap

The legacy gate review (`docs/phase0/gate_review.md`) is still AMEND because the live Postgres isolation suite has not run. That work maps to roadmap Phase 3 (tenancy) and does not block this Phase 0. See `docs/AUDIT.md`.
