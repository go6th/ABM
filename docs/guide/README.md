# Project guide: Account Intelligence ("ABM Generator")

An easy-to-follow explanation of the whole project: what it is, how a research runs from the
click to the finished files, which code does which job, which setting controls what, how to run
and release it, and what is currently broken.

*Written 2026-10-29 from the code as it stands, by reading the source and the staging logs. The
older technical documents in `docs/` and `PROJECT_STRUCTURE.md` go deeper; this guide is the
plain-language way in.*

## Read in this order

| # | Page | You learn |
|---|---|---|
| 1 | [What this project is](01-what-this-project-is.md) | the product in a minute, the three services, the vocabulary |
| 2 | [How a research runs](02-how-a-research-runs.md) | the workflow: start → slices → the research graph → grading → files; every way a run can stop; the auto-resume rules; a real worked example |
| 3 | [Code map](03-code-map.md) | every folder and package, what it is for, its key files, and "find the code by question" |
| 4 | [Config map](04-config-map.md) | the ~70 YAML files and the `RESEARCH_CAPS` switch, grouped by purpose |
| 5 | [Running and deploying](05-running-and-deploying.md) | run it locally, run the tests safely, release to staging, and a checklist for "why did this research stop?" |
| 6 | [Known problems](06-known-problems.md) | every error we have kept, with evidence, status (fixed / in the tree / open) and next steps |

## The three sentences to remember

1. **A research is a long job cut into 27-minute slices**; progress is saved between slices, and
   anything already paid for is replayed for free.
2. **The AI proposes, code decides**: every fact needs a word-for-word quote, and the grade
   (Confirmed / Probable / Hypothesis / Lead) is assigned by rules, never by the model.
3. **When something stops, the logs name the step**: `slice.ended` says which stage and whether it
   moved; `run.finished` says why it ended.

## Where the deeper documents are

* `PROJECT_STRUCTURE.md` — the folder-by-folder map written after the 2026-10-16 audit.
* `docs/ARCHITECTURE.md`, `docs/workspace-how-it-works.md`, `docs/product-overview.md` — design.
* `docs/architecture/module-map.md` (generated), `docs/WIRING.md` (screens → API, generated),
  `docs/ENVIRONMENT.md` (every environment variable, generated).
* `DEPLOYMENT.md`, `RUNBOOK.md`, `docs/deploy/` — release and operations.
* `BUILD_LOG.md` — the history of every work package and decision.

## Keeping this guide true

The pages cite file and function names so each claim can be checked. When code changes, update the
page that names it. The facts that change fastest are the **status of problems** (page 6) and the
**deployed worker revision** (page 6 header). The guide marks anything it could not verify as
"unverified".
