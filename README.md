# Account Intelligence

Turns one company name into a sourced, graded account brief and a deduplicated senior people list. This repository takes it from a single-operator skill to an automated, multi-tenant service, following `Account_Intelligence_Production_Roadmap.pdf`.

**Current phase: Phase 0, decisions and foundations.** Status: [`docs/PHASE0_STATUS.md`](docs/PHASE0_STATUS.md).

How to build here: [`.claude/skills/account-intelligence-builder/SKILL.md`](.claude/skills/account-intelligence-builder/SKILL.md). Audit of what existed before: [`docs/AUDIT.md`](docs/AUDIT.md).

## Layout

```
app/                application package (settings, rules, golden, llm, brokers, people, benchmarks, workflows)
config/             versioned rules: checklist (33 rows), grading, routing, output spec, models, people scope
fixtures/recorded/  recorded broker responses (synthetic) used by the test doubles
tests/app/          tests for app/
tests/evals/golden/ golden accounts (synthetic placeholders until the analyst adds real ones)
infra/terraform/    dev, staging, production
scripts/deploy.sh   one-command deploy
docs/               decision log, data handling draft, Phase 1 backlog, status, audit
src/abm_generator/  legacy code from the earlier roadmap (kept, not extended)
```

## Quick start

```
make venv                 # .venv with dev dependencies (Python 3.12+)
make app-check            # ruff, format check, mypy strict, pytest (what CI runs)
make bench-model-split    # offline model split harness -> build/benchmarks
make bench-people         # offline people source harness -> build/benchmarks
make temporal-up worker   # local Temporal dev server and worker (needs docker)
make hello                # run the hello-world workflow
DRY_RUN=1 make deploy-staging
```

Nothing in Phase 0 calls a live broker or model. Keys needed for live runs are listed in `docs/PHASE0_STATUS.md`.

## Rules that never bend

The system never sends outreach. Client-facing output never names data brokers, credit counts or costs. Every fact carries a grade, a URL and the exact sentence relied on; only Confirmed facts may be stated to a prospect. Full list: the skill, section 3.
