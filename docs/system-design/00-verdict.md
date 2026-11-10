# 00 · Verdict: is this architecture sound?

Scope: branch `feat/final-product` **including uncommitted changes in the working tree** (42 modified, 7 untracked files), and the live Google Cloud project `abmgenerator` read with `gcloud` on 2026-11-10. At audit time the live environment was switched off (database stopped, queues and most schedules paused). It is called "staging" but holds client accounts, so it is judged as production.

## The answer

**The core design is sound. The way it is run is not yet ready for paying customers.** The big choices are the ones an experienced team would make. A backend-for-frontend keeps the api and worker off the internet. Row-level security is forced on every tenant table, with the tenant set per transaction. Cloud Tasks drives work with leases, named tasks and a per-call spend ledger. A load balancer with Cloud Armor sits at the edge, and the database, Redis and egress are all on private networking. Tenant isolation held up under inspection. We found no path that reads another tenant's data, no unauthenticated data endpoint, and no way to reserve or settle credits twice.

What fails is the operational layer around that core:
- The only environment serves customers with spend caps switched off.
- A tenant deletion would silently erase that tenant's billing ledger.
- Seventeen alert rules send to nobody.
- The database and Redis are single points of failure.
- Personal data about named people cannot be deleted on request.
- What runs was built from a laptop, outside CI.

Fix the two P0 findings and the phase-1 list first. That is about two weeks of work, mostly configuration. Finish phase 2 before an enterprise security review.

## Scorecard

| Dim | Area | Verdict | One line |
|---|---|---|---|
| A | Edge and networking | Needs work | LB, Cloud Armor, private networking and fixed egress are good; there is no owned domain, and project leftovers remain. |
| B | Routing and API design | Needs work | The default-deny catalog, validated contract and BFF are good. There are three idempotency styles, error detail is lost, and two routers rely on auth in each handler. |
| C | Authentication | Needs work | SSO, sessions and CSRF are well built. SSO domain claims are unverified and staff MFA is assumed. |
| D | Authorisation and multi-tenancy | Needs work | RLS isolation is sound. Staff can bypass support grants, and the audit log is not tamper-evident. |
| E | Service architecture | Needs work | Leases and idempotent tasks are good. Runs can be ended or lose their next slice between slices. |
| F | Data layer | Needs work | Tenant deletion cascades the ledger (P0), deletion and retention are missing, and data-changing migrations can silently touch no rows. |
| G | External integrations | Needs work | The research connector pipeline is strong. Ask web tools, model calls, notifications and billing are not covered by it. |
| H | Language-model architecture | Needs work | Tools are read-only and tenant-scoped. Caps are off, the data-class guard is off and model ids are aliases. |
| I | Scalability and performance | Needs work | About 6 concurrent runs is the ceiling, provider limits are shared platform-wide, and the database has 1 vCPU. |
| J | Reliability | Needs work | Credits are effectively counted exactly once. The database is zonal, Redis is a single node and there is one region. |
| K | Security | Needs work | Secrets handling and upload sandboxing are good. Personal accounts hold Owner, scanning is off and the people-data position is not settled. |
| L | Observability and operations | **Missing** | Logs, metrics and traces exist, but no alert reaches a person and there is no error tracking. |
| M | Delivery | Needs work | CI is extensive but does not gate releases. There is one environment, and drift against Terraform is not measured. |
| N | Frontend architecture | Sound | Authorisation is enforced by the API, there are no public environment variables, the nonce CSP is strict and streaming is resilient. |

## P0 findings

| ID | Finding | Fix | Effort |
|---|---|---|---|
| SD-01 | Tenant deletion would cascade-delete the credit ledger (`config/deletion.yaml:108,171`; `migrations/0053_replica.sql:130`) | Change the foreign key to `RESTRICT`, scrub jobs instead of deleting them, and make the deletion-plan check aware of foreign keys | S |
| SD-02 | Spend is uncapped on the live services; spend above the reservation is absorbed rather than billed (`RESEARCH_CAPS=off`, `TOKEN_GUARD=off` in `gcloud run services list`; `replica/credits.py:408`) | Turn caps on, add a hard ceiling per run and per Ask thread, and set provider-console limits | S |

## Top ten findings

| # | ID | Sev | Finding |
|---|---|---|---|
| 1 | SD-02 | P0 | Spend is uncapped, and spend above the reservation is absorbed. |
| 2 | SD-01 | P0 | Tenant deletion erases the credit ledger by cascade. |
| 3 | SD-03 | P1 | 17 alert policies have 0 notification channels, and there is no error tracking. |
| 4 | SD-04 | P1 | There is no deletion path for personal data; retention is not enforced and checkpoints are kept forever. |
| 5 | SD-05 | P1 | Tenant files and people data go to Anthropic and Ollama Cloud with the guard off and retention terms unverified. |
| 6 | SD-06 | P1 | Cloud SQL is zonal with 1 vCPU, 7-day backups and no cross-region copy. |
| 7 | SD-09 | P1 | Releases are laptop builds that include uncommitted patches, with no tests or scans; drift is unmeasured. |
| 8 | SD-08 | P1 | Runs can be ended while queued, or lose their next slice when a task name is swallowed. |
| 9 | SD-07 | P1 | Redis is BASIC with no AUTH, and its loss stops every paid call. |
| 10 | SD-10 / SD-13 | P1 | Any tenant can claim another company's SSO domain; staff MFA is assumed, and a personal Gmail is Owner and operator. |

The full register has 38 findings: 2 P0, 18 P1, 18 P2 (`03-findings.md`).

## Items we could not verify

| Item | Command or access needed |
|---|---|
| Live database roles, forced RLS, IAM database users, connection counts (the instance was stopped) | Start `ai-staging-pg`, then run `gcloud sql users list --instance ai-staging-pg`, `psql -c "\du"`, `SELECT relname FROM pg_class WHERE relrowsecurity AND NOT relforcerowsecurity;` and `SELECT usename, count(*) FROM pg_stat_activity GROUP BY 1;` |
| Whether the row changes in 16 past migrations were applied (SD-18) | One `SELECT` per migration for the condition its `UPDATE` targeted, on the live DB |
| Terraform drift | Project owner: `cd infra/envs/staging && terraform plan -lock=false` |
| Billing budgets | Billing admin: `gcloud billing budgets list --billing-account=<id>` |
| Model providers' data retention | Anthropic and Ollama account terms and settings |
| seccomp under Cloud Run gen2 | Run the sandbox self-test on a worker revision and read its log |
| Ask SSE through Next.js compression; 100 MB library upload against Cloud Run's 32 MiB HTTP/1 limit; 2 GiB download parts; Node's 300 s header timeout | Live requests on staging (`curl -N`, a 100 MB upload) |
| Whether the nightly eval workflow runs | `gh run list --workflow eval.yml` |
| Organisation policies and Security Command Center | Org admin: `gcloud resource-manager org-policies list --organization 739140005194` |

## Documents

- `01-as-built.md`: inventory, diagrams 1–8, the full route table of 229 routes, and the gcloud commands run
- `02-audit.md`: dimensions A–N and answers to the 20 leads
- `03-findings.md`: findings register
- `04-target-architecture.md`: target diagrams, before-and-after table, roadmap and decision records
- `05-qa.md`: 38 questions and answers
- `06-concepts.md`: glossary
