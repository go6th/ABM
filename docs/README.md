# Documentation

The living documentation. Point-in-time audits, build prompts and superseded guides were moved
to `/home/sakib/unused code ABM generator/docs-history/` on 2026-10-16; what is here describes the system as it is.
The folder map of the whole repository is `../PROJECT_STRUCTURE.md`.

## Start here

| Document | What it is |
| --- | --- |
| [guide/](guide/README.md) | **the plain-language project guide**: what it is, how a research runs step by step, which code does what, the config map, running and releasing it, and the known problems |
| [ARCHITECTURE.md](ARCHITECTURE.md) | the system as built: services, request flow, tenancy and private workspaces, where data lives |
| [workspace-how-it-works.md](workspace-how-it-works.md) | the workspace end to end, in full detail: ingest, the document/table store, the Ask tool loop, and the one model layer every surface reads |
| [product-overview.md](product-overview.md) | the product: problem, workflow, grading, refresh, billing, where the source documents disagree |
| [build/REPLICA_PROMPT.md](build/REPLICA_PROMPT.md) | the product specification in force (the design prototype as a working product, with the owner's decisions) |
| [design/claude-design/](design/claude-design/) | the design prototype every screen is built to (17 HTML pages) |
| [open-items.md](open-items.md) | what is still open, by owner |
| [decisions/](decisions/) | the architecture decision records (ADR-0001 …), conflicts between sources, open decisions |

## Generated references (never edit by hand; `scripts/README.md` says what regenerates each)

`ENVIRONMENT.md` (every variable), `WIRING.md` (screen call sites to API operations),
`api/openapi.json` (the API contract), `architecture/module-map.md`, `architecture/loops.md`,
`workflow/stage-map.md`, `tracker/transitions.md`, `jobs/`, `crosscutting/CONFORMANCE.md`,
`parallel/simulation-report.md`, `benchmarks/model-split.md`.

## By subject

| Folder | Covers |
| --- | --- |
| `abm/` | the ABM research skill as agents: source map, agent catalogue, conversions, lessons, reconciliation, the reader's guide |
| `architecture/`, `data_layer/`, `workflow/`, `tracker/` | module map, loops, input modes; table conventions; the 13 stages; the tracker |
| `grading/`, `jobs/`, `people/`, `exports/`, `spend/`, `parallel/`, `verify/`, `evals/`, `routing/` | one folder per subsystem: rules, rubrics, counts, mutation evidence, decisions |
| `testing/` | mutation testing: how to run it, the 90 percent bar, the score per file and the list of equivalent mutants the gate reads |
| `providers/` | the provider contracts: per-provider notes, cassettes and golden replies, vendor source extracts (the only place vendor documents live) |
| `deploy/` | `ARCHITECTURE.md` (what Terraform builds, sizes, cost), `RUNBOOK.md` (setup, deploy, rollback, rotation, restore), `SECURITY_AUDIT.md`, `agents/` (the Phase 4C hardening notes the code comments cite) |
| `runbooks/` | one runbook per alert (linted by `scripts/check_runbooks.py`) plus `queries/` |
| `observability/`, `metrics/`, `oncall/`, `postmortems/` | the telemetry design, metric definitions, on-call rota and severity, the postmortem template |
| `security/`, `privacy/`, `risk/` | threat model, pentest scope, release checklist, SOC 2 plan; processing record, lawful basis, DPA notes, subprocessors; risk and decision registers |
| `phase3/`, `phase4/` | the tenant-plane and automation design documents and exit criteria |
| `opendoor/`, `frontend/`, `spikes/`, `crosscutting/` | the signal webhook contract; route maps; Phase 0 spike evidence; conformance |
| `CLAIMS.md`, `people-source-comparison.md`, `data-handling-people-data.md`, `infrastructure-plan.md` | claim labels, the people-source protocol, people data handling, the environment pointer |

`sources/` holds the owner's confidential source documents and is excluded from Git.
