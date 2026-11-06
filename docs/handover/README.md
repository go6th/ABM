# Handover

Written on 2026-11-03 from the code on disk (branch `feat/final-product`, including the
uncommitted fix-prompt work A to F, ADR-0106 to ADR-0136). Every statement on these pages carries
a `path:line` or `path` reference that was checked by reading the code; files that were being
edited while the pages were written are cited by file and function name instead. Where an ADR or
an older document disagrees with the code, the page says so in its last section: the code wins.

This set replaces `docs/guide/` (a 29 October snapshot, parked in
`unwanted-things/docs-guide-2026-10-29/`).

| # | Page | What it covers |
|---|---|---|
| 1 | [Architecture and planes](01-architecture-and-planes.md) | the three services (api, worker, web), `SERVICE_ROLE` planes, the packages under `src/`, import-linter layering, the Cloud Run / Tasks / SQL shape |
| 2 | [Request path and auth](02-request-path-and-auth.md) | web BFF to api, sessions, operator sessions, SAML through the BFF, the route catalogue, client feature flags |
| 3 | [Research run lifecycle](03-research-run-lifecycle.md) | research jobs and `POST /v1/runs`, which graph runs, slices, leases, the tracker verdict, the sweeper's six jobs, schedule ticks, `stall_after_s` |
| 4 | [Model routing](04-model-routing.md) | `llm/route.py`, `resolve_model`, the one price table, G11/G12, the data-class guard |
| 5 | [Spend and caps](05-spend-and-caps.md) | research caps and the token guard in the cloud, the dollar cap, the 3x guard, `entity_lock`, replay keys |
| 6 | [Data and isolation](06-data-and-isolation.md) | RLS, the runtime roles, the system-scope allow-list, migrations and the isolation suite |
| 7 | [Documents and sandbox](07-documents-and-sandbox.md) | uploads, document processing, the parser sandbox, retrieval, file-mode runs, Ask |
| 8 | [Exports and gate](08-exports-and-gate.md) | export builders, the delivery gate (withhold per file), leak checks, rebuilds, the repair stage |
| 9 | [Ops](09-ops.md) | manual releases (Cloud Build + gcloud), Terraform envs, scheduler entries, alerts, local dev, tests |
| 10 | [Known gaps](10-known-gaps.md) | retention not enforced, the archive lock waiting on the owner, monitoring not wired, Workflow v1 deprecation, and the rest |

Related: `docs/decisions/` (ADRs), `docs/registers/` (registers no code reads),
`docs/audits/purge-2026-11.md` (what prompt F parked and why), `unwanted-things/README.md`
(everything parked, with the reason).
