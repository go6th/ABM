# Specification traceability

This matrix compares the implementation with the three local source documents. The source files
are intentionally ignored by Git and remain in `docs/sources/`.

**Status key:** Done means the mapped check was run and passed; Partial means some code or tests
exist but the full requirement is not proven or is incomplete; Missing means no implementation was
found; Blocked-by-external means verification needs real credentials, a client, a legal decision,
or cloud resources. Statuses are conservative and will be updated in `docs/RELEASE_REPORT.md` after
the gates run.

## PRD: product requirements and process

| ID | Requirement | Source | Implementation and proof | Status |
|---|---|---|---|---|
| PRD-G1 | Every source type is visible in a coverage tracker | PRD 3.1 | `config/checklist.yaml`; `src/agents/manifest.py`; `tests/agents/` | Partial |
| PRD-G2 | Corrections become a checklist item or lesson | PRD 3.1 | `src/agents/lessons.py`; `config/skill/`; archive reconciliation remains open | Partial |
| PRD-G3 | Every claim is traceable and graded | PRD 3.1, 3.4 | `src/account_intelligence/grading/`; `src/verify/`; `tests/grading/`, `tests/verify/` | Partial |
| PRD-G4 | Refresh reuses source IDs and reports changes | PRD 3.1, 4.5 | `src/refresh/`; `src/account_intelligence/grading/repo.py`; `tests/refresh/` | Partial |
| PRD-G5 | One approved cap governs paid calls and spend is reported | PRD 3.1, 3.4 | `src/spend/`; `src/account_intelligence/connectors/ledger.py`; `tests/spend/`, `tests/connectors/` | Partial |
| PRD-I1 | Account name and domain are required; careers URL is visited and verified | PRD 3.3 | `src/account_intelligence/api/routes/accounts.py`; `src/account_intelligence/api/domains.py`; `tests/api/` | Partial |
| PRD-I2 | Pursuing client is asked once and missing relationships stay blocked | PRD 3.3 | `src/agents/setup/`; `src/agents/manifest.py`; `tests/agents/` | Partial |
| PRD-I3 | Default lane set is Salesforce, ServiceNow, SAP, Tableau, Veeva, and Data and AI | PRD 3.3 | `config/lanes.yaml`; `src/agents/setup/`; lane coverage tests | Partial |
| PRD-I4 | One cap covers all tools; breakdown is shown before approval | PRD 3.3 | `src/spend/`; `src/agents/setup/`; `tests/spend/`, `tests/agents/` | Partial |
| PRD-I5 | People scope defaults to Director and above, with named exclusions | PRD 3.3 | `config/people_rubric.yaml`; `src/people/`; `tests/people/` | Partial |
| PRD-I6 | Pre-pulled files can replace a provider call at zero cost | PRD 3.3 | `src/spend/exhaustion.py`, `src/spend/upload.py`; `tests/spend/` | Partial |
| PRD-F1 | Every coverage row ends with a status and evidence of query, tool, and count | PRD 3.4 | `src/agents/manifest.py`; `src/account_intelligence/tracker/`; `tests/agents/`, `tests/tracker/` | Partial |
| PRD-F2 | Search matches remain Leads until read; job postings are read and classified | PRD 3.4 | `src/account_intelligence/grading/product.py`; `src/jobs/`; `tests/grading/`, `tests/jobs/` | Partial |
| PRD-F3 | A Lead gets three distinct queries across two tool categories | PRD 3.4 | `src/account_intelligence/grading/leads.py`; `config/skill/run.yaml`; `tests/grading/` | Partial |
| PRD-F4 | Absence of evidence is a Hypothesis, never a conclusion | PRD 3.4 | `src/account_intelligence/grading/product.py`; `tests/grading/test_unread_match.py` | Partial |
| PRD-F5 | Company baseline uses primary releases, newsroom, leadership, and recent earnings calls | PRD 3.4 | `src/agents/pull/`; `config/checklist.yaml`; synthetic run tests | Partial |
| PRD-F6 | Job search covers lane keywords and 12 months of open and closed postings | PRD 3.4 | `src/jobs/`; `config/jobs.yaml`; `tests/jobs/` | Partial |
| PRD-F7 | Technology detections are pulled once and cross-checked with postings and dates | PRD 3.4 | `src/jobs/`, `src/agents/pull/`; `tests/agents/`, `tests/jobs/` | Partial |
| PRD-F8 | Person-name research runs after people selection for the top ten people | PRD 3.4 | `src/people/`, `src/agents/graph.py`; `tests/people/`, `tests/agents/` | Partial |
| PRD-F9 | Review sites and vendor communities are recorded even when empty | PRD 3.4 | `config/checklist.yaml`; `src/agents/manifest.py`; `tests/agents/` | Partial |
| PRD-F10 | People are deduplicated by profile URL, then name; broker-grid people are not pulled twice | PRD 3.4 | `src/people/dedupe.py`, `src/people/pipeline.py`; `tests/people/` | Partial |
| PRD-F11 | Senior public profiles include role, tenure, certifications, location, and source | PRD 3.4 | `src/people/`; `tests/people/`; provider-backed completeness not run | Partial |
| PRD-F12 | Alumni, tenure, and certification counts are computed in code with a stated base | PRD 3.4 | `src/people/counts.py`; `tests/people/` | Partial |
| PRD-F13 | Priority A people posts are limited to recent activity where dates exist | PRD 3.4 | `src/people/`; `config/people_rubric.yaml`; `tests/people/` | Partial |
| PRD-F14 | Summary contains a separate Findings from LinkedIn section | PRD 3.4 | `src/exports/`; `tests/exports/`; rendered output gate not run | Partial |
| PRD-F15 | Grades follow Confirmed, Probable, Hypothesis, and Lead definitions | PRD 3.4 | `src/account_intelligence/grading/product.py`; `config/grading_policy.yaml`; grading tests | Partial |
| PRD-F16 | Every fact, posting, article, and person row has a URL; every count names its base | PRD 3.4 | `src/exports/`, `src/jobs/`, `src/people/`; `tests/exports/`, `tests/jobs/`, `tests/people/` | Partial |
| PRD-F17 | Plain language, first-use abbreviations, and sensitive-finding wording are enforced | PRD 3.4 | `config/export_rules.yaml`; `src/exports/policy.py`; `tests/exports/` | Partial |
| PRD-F18 | Salesforce-partner outputs do not frame a customer as leaving or failing Salesforce | PRD 3.4 | `config/export_rules.yaml`; `src/exports/policy.py`; `tests/exports/` | Partial |
| PRD-F19 | Account explanations given in chat are also delivered in a file | PRD 3.4 | API delivery path and `src/exports/`; no chat product exists in this repository | Missing |
| PRD-D1 | Account Summary Word file contains narrative, graded tables, people, hypotheses, questions, and tracker | PRD 3.4 | `src/exports/`; `tests/exports/`; layout gate not run | Partial |
| PRD-D2 | Sources and Evidence Word file contains stable IDs, links, dates, grades, and relied-on text | PRD 3.4 | `src/exports/`; `tests/exports/` | Partial |
| PRD-D3 | People List workbook is deduplicated and reports exclusions and counts | PRD 3.4 | `src/exports/excel/`, `src/people/`; `tests/exports/`, `tests/people/` | Partial |
| PRD-D4 | Job Postings workbook includes 12 months, class, platform sentence, description, and link | PRD 3.4 | `src/exports/excel/`, `src/jobs/`; `tests/exports/`, `tests/jobs/` | Partial |
| PRD-D5 | Tech Stack and Teams workbook includes detections, dates, and posting cross-checks | PRD 3.4 | `src/exports/excel/`, `src/datamodel/`; `tests/exports/` | Partial |
| PRD-D6 | Workspace ZIP contains the shareable artifacts and restore metadata, with no internal-only file | PRD 3.4 | `src/exports/`; `config/export_rules.yaml`; `tests/exports/` | Partial |
| PRD-D7 | Internal Only Word output is separate and excluded from the shared ZIP | PRD 3.4 | `src/agents/production/delivery.py` documents the absent builder; OI-ABM-4 | Missing |
| PRD-Q1 | Word deliverables render to PDF and table rows survive page breaks | PRD 3.4 | `src/gate/`, `src/exports/`; renderer adapters remain `NotConfigured` | Partial |
| PRD-Q2 | Export checks find zero orphan citations, banned punctuation, stale phrases, and excluded names | PRD 3.4 | `src/gate/`, `scripts/scan_client_output.py`; `tests/exports/` | Partial |
| PRD-Q3 | Workbooks recalculate without formula errors and totals match row counts | PRD 3.4 | `src/exports/excel/`, `src/gate/`; `tests/exports/` | Partial |
| PRD-Q4 | Tracker totals are recomputed from rows | PRD 3.4 | `src/account_intelligence/tracker/`; `tests/tracker/` | Partial |
| PRD-Q5 | Narrative statements map to graded rows and source IDs | PRD 3.4 | `src/exports/policy.py`, `src/gate/`; `tests/exports/` | Partial |
| PRD-Q6 | Third-party names, costs, and cross-account names are scanned from packages and metadata | PRD 3.4 | `scripts/scan_client_output.py`; `tests/security/`, `tests/exports/` | Partial |
| PRD-C1 | The system never sends outreach or overwrites client records | PRD 3.5 | No send route; create-only connector contracts; `tests/phase4/`, `tests/connectors/` | Partial |
| PRD-C2 | Client outputs never name brokers, costs, client staff, or another account | PRD 3.5 | `src/exports/policy.py`, `scripts/scan_client_output.py`; leak tests | Partial |
| PRD-C3 | No call recordings or client staff names appear in client outputs | PRD 3.5 | `src/exports/`, `src/privacy/`; internal source adapters are incomplete | Partial |
| PRD-C4 | One account per file set; another account's name is absent | PRD 3.5 | Tenant-scoped export builder; `tests/exports/`, `tests/isolation/` | Partial |
| PRD-C5 | Drive reads remain within client-named folders; contracts are cited at seller level | PRD 3.5 | `src/connectors_client/` has `NotConfigured` adapters; D-65 | Missing |
| PRD-C6 | Word files use US Letter, suitable margins, and the PRD punctuation rules | PRD 3.5 | `src/exports/word/`, `config/export_rules.yaml`; render checks pending | Partial |
| PRD-M1 | Coverage completeness is measured from High rows and proof | PRD 3.6 | `src/account_intelligence/tracker/`; `tests/tracker/` | Partial |
| PRD-M2 | Corrections per run and claim traceability are measured | PRD 3.6 | `src/pilot/`, `src/metrics/`, `src/account_intelligence/grading/`; tests exist | Partial |
| PRD-M3 | Turnaround and spend are measured from run and ledger records | PRD 3.6 | `src/obs/`, `src/spend/`; `tests/obs/`, `tests/spend/` | Partial |
| PRD-M4 | Seller usefulness is instrumented from client feedback | PRD 3.6 | `src/pilot/`; `tests/phase3/`; pilot evidence not run | Partial |

### PRD 4.2: the 13 research steps

| ID | Requirement | Source | Implementation and proof | Status |
|---|---|---|---|---|
| PRD-P01 | Setup reads the checklist and lessons, validates domain/careers URL, proposes one cap | PRD 4.2 step 1 | `src/agents/setup/`, `src/agents/graph.py`; `tests/agents/` | Partial |
| PRD-P02 | Pass 1 gathers relationship history, contacts, and fit | PRD 4.2 step 2 | `src/agents/`; relationship inputs remain incomplete | Partial |
| PRD-P03 | Baseline reads recent primary releases, newsroom, leadership, and earnings voice | PRD 4.2 step 3 | `src/agents/pull/`, `config/checklist.yaml`; synthetic tests | Partial |
| PRD-P04 | News feed leads are verified at source or marked feed-only | PRD 4.2 step 4 | `src/agents/pull/`, `src/account_intelligence/grading/`; tests | Partial |
| PRD-P05 | Jobs are searched by lane, read, classified, and counted by status | PRD 4.2 step 5 | `src/jobs/`; `tests/jobs/` | Partial |
| PRD-P06 | Technology is pulled once, lane-filtered, dated, and cross-checked | PRD 4.2 step 6 | `src/agents/pull/`, `src/datamodel/`; tests | Partial |
| PRD-P07 | People are searched by title, gap-filled, merged, deduplicated, excluded, and prioritized | PRD 4.2 step 7 | `src/people/`; `tests/people/` | Partial |
| PRD-P08 | Person searches run after the people list; review and communities are checked | PRD 4.2 step 8 | `src/agents/`, `config/checklist.yaml`; tests | Partial |
| PRD-P09 | Leads receive the required queries and are rechecked when evidence supports them | PRD 4.2 step 9 | `src/account_intelligence/grading/leads.py`; tests | Partial |
| PRD-P10 | Distillation grades claims and gives each Hypothesis a confirming question | PRD 4.2 step 10 | `src/agents/distill/`, `src/account_intelligence/grading/`; tests | Partial |
| PRD-P11 | Builders create the standing files and workspace ZIP | PRD 4.2 step 11 | `src/exports/`; `tests/exports/`; production renderer remains partial | Partial |
| PRD-P12 | Quality gate blocks bad output and reruns after a fix | PRD 4.2 step 12 | `src/gate/`; `tests/exports/`, `tests/phase4/` | Partial |
| PRD-P13 | Delivery reports findings, fixes, open work, and spend; lessons are updated | PRD 4.2 step 13 | `src/agents/production/delivery.py`; `src/agents/lessons.py`; tests | Partial |

### PRD Appendix A: coverage tracker rows

All 33 tracker keys are seeded by `config/checklist.yaml`; the manifest and tracker tests prove
shape and terminal statuses. Real-source completeness is not proven in this local environment.

| ID | Coverage row | Source | Implementation and proof | Status |
|---|---|---|---|---|
| PRD-COV01 | Prior customer status, past interactions, prior pursuits | PRD Appendix A | `config/checklist.yaml`; `src/agents/manifest.py`; `tests/agents/` | Partial |
| PRD-COV02 | Connections, alumni overlaps, former colleagues | PRD Appendix A | checklist, `src/people/`; `tests/people/` | Partial |
| PRD-COV03 | Platform vendor account team | PRD Appendix A | checklist, `src/agents/`; `tests/agents/` | Partial |
| PRD-COV04 | Profiles and posts of key contacts | PRD Appendix A | checklist, `src/people/`; `tests/people/` | Partial |
| PRD-COV05 | Basic fit check | PRD Appendix A | checklist, `src/agents/setup/`; `tests/agents/` | Partial |
| PRD-COV06 | Company news and press releases | PRD Appendix A | checklist, `src/agents/pull/`; tests | Partial |
| PRD-COV07 | Earnings releases and filings | PRD Appendix A | checklist, `src/agents/pull/`; tests | Partial |
| PRD-COV08 | Leadership changes in the last six months | PRD Appendix A | checklist, `src/agents/pull/`; tests | Partial |
| PRD-COV09 | Job board search by lane | PRD Appendix A | checklist, `src/jobs/`; `tests/jobs/` | Partial |
| PRD-COV10 | Job postings read and classified | PRD Appendix A | `src/jobs/`; `tests/jobs/` | Partial |
| PRD-COV11 | Twelve months of open and closed job postings | PRD Appendix A | `src/jobs/`, `config/jobs.yaml`; tests | Partial |
| PRD-COV12 | Technology detections | PRD Appendix A | `src/datamodel/`, `src/agents/pull/`; tests | Partial |
| PRD-COV13 | People by platform | PRD Appendix A | `src/people/`; `tests/people/` | Partial |
| PRD-COV14 | Full profiles of senior people | PRD Appendix A | `src/people/`; `tests/people/` | Partial |
| PRD-COV15 | Posts and comments of priority people | PRD Appendix A | `src/people/`; `tests/people/` | Partial |
| PRD-COV16 | Executive voice from earnings calls | PRD Appendix A | `src/agents/pull/`; tests | Partial |
| PRD-COV17 | Financial baseline verified | PRD Appendix A | `src/agents/pull/`, `src/verify/`; tests | Partial |
| PRD-COV18 | Vendor customer stories | PRD Appendix A | checklist, `src/agents/pull/`; tests | Partial |
| PRD-COV19 | Verification of items graded Lead | PRD Appendix A | `src/account_intelligence/grading/leads.py`; tests | Partial |
| PRD-COV20 | Customers and partners matched to client references | PRD Appendix A | checklist, tenant proof library; real client data blocked | Blocked-by-external |
| PRD-COV21 | Executive speaking, upcoming and past | PRD Appendix A | checklist and agent pull plan; provider proof absent | Partial |
| PRD-COV22 | Podcasts | PRD Appendix A | checklist and agent pull plan; provider proof absent | Partial |
| PRD-COV23 | YouTube | PRD Appendix A | checklist and agent pull plan; provider proof absent | Partial |
| PRD-COV24 | Medium and Substack | PRD Appendix A | checklist and agent pull plan; provider proof absent | Partial |
| PRD-COV25 | Review sites | PRD Appendix A | checklist and terminal `None` handling; tests | Partial |
| PRD-COV26 | Vendor communities and marketplaces | PRD Appendix A | checklist and terminal `None` handling; tests | Partial |
| PRD-COV27 | Regulatory and quality events | PRD Appendix A | checklist and agent pull plan; provider proof absent | Partial |
| PRD-COV28 | Financial position against peers | PRD Appendix A | checklist and agent pull plan; provider proof absent | Partial |
| PRD-COV29 | Competitor moves and peer KPIs | PRD Appendix A | checklist and agent pull plan; provider proof absent | Partial |
| PRD-COV30 | Major holders and activist activity | PRD Appendix A | checklist and agent pull plan; provider proof absent | Partial |
| PRD-COV31 | Events sponsored or exhibited at | PRD Appendix A | checklist and agent pull plan; provider proof absent | Partial |
| PRD-COV32 | Employee content, GitHub, and engineering blogs | PRD Appendix A | checklist and agent pull plan; provider proof absent | Partial |
| PRD-COV33 | Research papers | PRD Appendix A | checklist and agent pull plan; provider proof absent | Partial |

## SaaS design: sections 2–7

| ID | Requirement | Source | Implementation and proof | Status |
|---|---|---|---|---|
| SAAS-2.1-A | Tenant users sign in and receive admin, seller, and viewer access | SaaS 2.1 | `src/identity/`, `src/account_intelligence/api/`; `web/e2e/tenant.spec.ts` | Partial |
| SAAS-2.1-B | Tenant profile stores organization type, lanes, proof library, writing rules, and account owner | SaaS 2.1 | `src/tenancy/`, `src/account_intelligence/api/routes/settings.py`; integration tests | Partial |
| SAAS-2.1-C | Users add accounts and configure lanes, people scope, cadence, and alert rules | SaaS 2.1 | account/settings APIs and `web/app/(client)/`; E2E coverage is partial | Partial |
| SAAS-2.1-D | First-run and monthly refresh budgets are tenant and account scoped | SaaS 2.1 | `src/spend/`, `src/metering/`; tests | Partial |
| SAAS-2.1-E | Users view briefs and download Word and Excel deliverables | SaaS 2.1 | `src/exports/`, client pages; `tests/exports/`, E2E partial | Partial |
| SAAS-2.1-F | Alerts are delivered by email, Slack, Teams, or digest | SaaS 2.1 | `src/alerts/`; email and Slack adapters are `NotConfigured`; Teams absent | Missing |
| SAAS-2.1-G | Plain-language questions are scoped to the tenant's accounts | SaaS 2.1 | `src/account_intelligence/rag/`; API integration and client flow not present | Missing |
| SAAS-2.2-A | Operators manage runs, refreshes, approval, and spend across tenants | SaaS 2.2 | `src/account_intelligence/api/operator.py`, `web/app/operator/`; connected screens partial | Partial |
| SAAS-2.2-B | Operators maintain shared checklist, lessons, and signals rules | SaaS 2.2 | repository configuration and agent code; operator edit UI absent | Missing |
| SAAS-2.2-C | Open Door remains a separate module reading account state and alerts | SaaS 2.2 | `src/opendoor/`; schema and read API only; no consumer or sending | Partial |
| SAAS-3.1-A | Tenant rows, files, jobs, and derived research are isolated by database RLS | SaaS 3.1 | migrations, `tenant_txn()`, `tests/isolation/`; full catalog run pending | Partial |
| SAAS-3.1-B | Tenant object prefixes and short-lived scoped URLs protect files | SaaS 3.1 | GCS adapter and tenant-prefix contracts; cloud bucket/key verification blocked | Blocked-by-external |
| SAAS-3.1-C | Broker calls and raw results are tenant scoped; shared public cache holds no derived data | SaaS 3.1, 3.3 | connector ledger and payload ports; public cache is not implemented | Missing |
| SAAS-3.1-D | Model context and retrieval never mix tenants | SaaS 3.1 | tenant-scoped run and RAG code; isolation tests exist | Partial |
| SAAS-3.1-E | Alerts, schedules, and budgets are tenant objects | SaaS 3.1 | `src/alerts/`, `src/refresh/`, `src/spend/`; tests | Partial |
| SAAS-3.1-F | Tenant audit is exportable and records reads, actions, delivery, and deletion | SaaS 3.1 | `src/audit/`; `tests/phase3/`; archive and production viewer partial | Partial |
| SAAS-3.1-G | Deletion removes derived and raw tenant data and is confirmed; public cache remains | SaaS 3.1 | `src/privacy/`, `config/deletion.yaml`; tombstone migration 0043; external key erase not proven | Partial |
| SAAS-3.1-H | Support access requires a logged, time-boxed tenant grant | SaaS 3.1 | `src/tenancy/`, operator view-as APIs; `tests/phase3/`; E2E partial | Partial |
| SAAS-3.2-A | Public, tenant configuration, derived, relationship, internal, and process data classes are distinct | SaaS 3.2 | visibility and tenancy model; `tests/isolation/`, `tests/exports/` | Partial |
| SAAS-3.3-A | Public cache is optional, off by default for enterprise, and reveals no tenant interest | SaaS 3.3 | cache sharing is not implemented | Missing |
| SAAS-4.1-A | Client portal, API, database, storage, orchestrator, adapters, builder, and gate are separated by ports | SaaS 4.1 | `src/account_intelligence/platform/`, app container, Next.js; contract tests | Partial |
| SAAS-4.1-B | Durable scheduler, alerting, billing, observability, and question answering are connected | SaaS 4.1 | refresh, alerts, spend, RAG, and observability packages; runtime composition incomplete | Partial |
| SAAS-4.2-A | Run creation reserves budget, records an outbox event, and returns 202 | SaaS 4.2 | run routes and lifecycle; `tests/integration/test_create_run.py` | Partial |
| SAAS-4.2-B | A run loads tenant state, executes setup and research, distills, builds, gates, and delivers | SaaS 4.2 | `src/account_intelligence/workflow/`; `tests/system/`, `tests/agents/` | Partial |
| SAAS-4.2-C | Refresh delta drives alert evaluation and delivery | SaaS 4.2, 5.2 | `src/refresh/`, `src/alerts/`; tests | Partial |
| SAAS-4.3-A | Model per step is configurable and evaluated before routing is enabled | SaaS 4.3 | `src/llm/`, `config/model_routing.yaml`; routing remains proposal-gated | Partial |
| SAAS-4.4-A | Each broker adapter meters calls, rate limits, retries, and normalizes responses | SaaS 4.4 | `src/account_intelligence/connectors/`; fixture tests; real adapters require keys and contracts | Partial |
| SAAS-4.5-A | Concierge and self-serve are phased; full tenant isolation is present from day one | SaaS 4.5 | tenant APIs and role checks exist; new-tenant self-serve workflow is incomplete | Partial |
| SAAS-5.1-A | Refresh cadence follows shelf life for jobs, news, technology, earnings, people, and reviews | SaaS 5.1 | `src/refresh/`, `config/shelf_life.yaml`; cadence tests | Partial |
| SAAS-5.2-A | Each refresh records new, expired, regraded, and count deltas with bases | SaaS 5.2 | `src/refresh/delta.py`, `src/refresh/history.py`; tests | Partial |
| SAAS-5.3-A | Alerts cover role postings, leadership, technology, news, speakers, people posts, and grade changes | SaaS 5.3 | `config/alerts_extra.yaml`, `src/alerts/`; `tests/phase4/` | Partial |
| SAAS-5.3-B | Budget and wallet notices reach the tenant admin without appearing as research alerts | SaaS 5.3 | `src/alerts/`, `src/metering/`; notifications lack a configured production sink | Partial |
| SAAS-6-A | Wallet reserves atomically, commits/refunds at terminal state, and reports cost by category | SaaS 6 | `src/spend/`, `src/account_intelligence/runs/lifecycle.py`; `tests/spend/`, `tests/runs/` | Partial |
| SAAS-6-B | Top-ups are webhook-idempotent and prices are plan/depth based | SaaS 6 | top-up integration is not configured; D-04 pricing is open | Missing |
| SAAS-6-C | Tenant views show categories and call counts, never provider names or raw costs | SaaS 6 | operator/client spend contracts and `src/exports/`; E2E scan exists | Partial |
| SAAS-7-A | Private-source connectors ingest only with tenant authorization into a tenant/account vault | SaaS 7.2–7.3 | `src/connectors_client/` ports are fail-closed; no production ingestion adapter | Missing |
| SAAS-7-B | Ingestion parses, classifies, detects entities, stores redaction maps, chunks, and embeds | SaaS 7.3 | Phase B pipeline not implemented | Missing |
| SAAS-7-C | Internal facts retain passage links and are never Confirmed without authoritative support | SaaS 7.3–7.4 | internal grading path is incomplete | Missing |
| SAAS-7-D | Client-facing output contains public evidence only; Internal Only output is separate | SaaS 7.5 | public/client scan exists; Internal Only file builder absent (OI-ABM-4) | Partial |
| SAAS-7-E | Internal source IDs and identifiers from redaction maps block delivery | SaaS 7.5 | `src/privacy/`, `src/gate/`; internal leak-map adapter absent | Missing |
| SAAS-7-F | Key management, retention, regional storage, and vendor data terms are verified | SaaS 7.6–7.7 | Terraform and secret ports exist; real keys, legal terms, and regional proof require owner inputs | Blocked-by-external |

## Architecture v2: isolation, lifecycle, API, billing, infrastructure, and SLOs

| ID | Requirement | Source | Implementation and proof | Status |
|---|---|---|---|---|
| ARCH-4-1 | Tenant identity comes only from a verified token or verified worker payload | ARCH 4.2, 9.2 | identity middleware and task route; `tests/identity/`, `tests/system/` | Partial |
| ARCH-4-2 | Runtime uses a non-owner role without BYPASSRLS | ARCH 4.2 | migration lint and database role setup; catalog check not yet recorded | Partial |
| ARCH-4-3 | Every tenant table forces RLS and every policy has USING and WITH CHECK | ARCH 4.2 | migration linter and RLS catalog script; `tests/isolation/` | Partial |
| ARCH-4-4 | Tenant context is transaction local and `tenant_txn()` is the only database entry point | ARCH 4.2 | `src/account_intelligence/db/`; isolation suite | Partial |
| ARCH-4-5 | Unset tenant context fails closed and policies use the wrapped tenant accessor | ARCH 4.2 | SQL policies and migration lint; `tests/isolation/` | Partial |
| ARCH-4-6 | Views are security-invoker; definer functions are narrow, reviewed, and listed | ARCH 4.2 | migrations and definer registry; `tests/isolation/` | Partial |
| ARCH-4-7 | Agents have no SQL tool and tools accept no tenant ID | ARCH 4.2 | `src/agents/contracts.py`, schemas and tool allowlists; `tests/agents/` | Partial |
| ARCH-4-8 | Checkpoints and vectors are tenant partitioned and pass planted-canary tests | ARCH 4.3 | checkpoint migrations, `src/account_intelligence/rag/`; isolation tests | Partial |
| ARCH-4-9 | Object keys and signed URLs are tenant/account scoped | ARCH 4.3 | GCS adapter contract tests; cloud IAM proof not run | Partial |
| ARCH-4-10 | Public renderer and independent leak detector block private facts | ARCH 4.1, 4.5 | export policy and denylist scans; `tests/exports/`, `tests/security/` | Partial |
| ARCH-5-1 | Run state transitions are optimistic and terminal states settle once | ARCH 5.1 | `src/account_intelligence/runs/`; `tests/runs/`, `tests/spend/` | Partial |
| ARCH-5-2 | Run creation is idempotent and atomically reserves funds with an outbox event | ARCH 5.2 | API routes, lifecycle, idempotency migration; integration tests | Partial |
| ARCH-5-3 | Checkpoint is source of truth; execution runs in bounded resumable slices | ARCH 5.2, 5.4 | workflow runner and LangGraph; `tests/system/`, `tests/spend/test_kill_resume.py` | Partial |
| ARCH-5-4 | One owner handles each retry class; stuck queued runs are reconciled | ARCH 5.3 | workflow retry policy and reconciler; chaos tests exist | Partial |
| ARCH-5-5 | Paid calls are ledgered by run and canonical arguments; retries reuse stored responses | ARCH 5.5 | `src/account_intelligence/connectors/ledger.py`; `tests/connectors/`, `tests/spend/` | Partial |
| ARCH-5-6 | Run caps are enforced before provider/model calls; wall time and call count are bounded | ARCH 5.6 | `src/spend/`, workflow guard; `tests/spend/`, `tests/system/` | Partial |
| ARCH-5-7 | Graph and state schema versions are pinned for resume | ARCH 5.7 | run schema and workflow version fields; `tests/runs/`, migration tests | Partial |
| ARCH-9-1 | Public API has typed OpenAPI schemas and versioned paths | ARCH 9.1, 9.3 | `src/account_intelligence/api/contract.py`, `docs/api/openapi.json`; snapshot test | Done |
| ARCH-9-2 | Roles restrict run creation, views, settings, billing, and audit | ARCH 9.2 | `src/account_intelligence/api/route_catalog.py`; `tests/api/`, `tests/isolation/` | Partial |
| ARCH-9-3 | Membership changes revoke access within the configured cache window | ARCH 9.2 | `src/identity/`; `tests/identity/`; browser revocation flow not run | Partial |
| ARCH-9-4 | Errors use problem details and idempotency keys are tenant-scoped | ARCH 9.3 | API middleware and run routes; API tests | Partial |
| ARCH-9-5 | Rate limits are tenant/user scoped and return Retry-After | ARCH 9.3 | `src/account_intelligence/api/guard.py`, Redis adapter; tests | Partial |
| ARCH-9-6 | Webhooks use HMAC, timestamp tolerance, retries, and event deduplication | ARCH 9.3 | webhook modules and tests; live sink blocked | Partial |
| ARCH-9-7 | Wallet reserve, commit, refund, top-up, and adjustment are ledger operations | ARCH 9.4 | `src/spend/`, `src/account_intelligence/runs/lifecycle.py`; tests | Partial |
| ARCH-9-8 | Nightly invariants check ledger balance and one settlement per terminal run | ARCH 9.4 | `src/spend/reconcile.py`, `src/metering/reconcile.py`; scheduled runtime not proven | Partial |
| ARCH-11-1 | Google Cloud is the deployment target per C11 | ARCH 11; C11 | `infra/` contains Google Cloud modules and environments | Partial |
| ARCH-11-2 | Cloud Run API, worker, migration job, Cloud SQL, storage, tasks, secrets, and observability are provisioned | ARCH 11; C11 | `infra/modules/` and `infra/envs/`; Terraform validation not yet run | Partial |
| ARCH-11-3 | Workloads use least-privilege service accounts and workload identity, never long-lived cloud keys | ARCH 11.5; C11 | Terraform identity and CI WIF configuration; external project setup required | Blocked-by-external |
| ARCH-11-4 | Egress blocks private/link-local targets, limits redirects, and protects metadata endpoints | ARCH 11.2 | `src/account_intelligence/connectors/security.py`; SSRF tests | Partial |
| ARCH-11-5 | Secrets are sourced from Secret Manager and are not printed or bundled to the browser | ARCH 11.5; C11 | secret adapter, web server-only variables, scan tests; runtime deployment not run | Partial |
| ARCH-11-6 | Database migrations are expand-and-contract and run before service rollout | ARCH 11.4 | Alembic migrations and CI; clean-database double-up gate not run | Partial |
| ARCH-11-7 | Disaster recovery meets RPO/RTO targets only after a cloud game-day | ARCH 11.3 | local restore/DR rehearsal docs and scripts; real regional failover requires GCP | Blocked-by-external |
| ARCH-13-1 | API availability is 99.9% monthly and read/create p99 is under 500 ms | ARCH 2.3, 13.1 | telemetry SLO config and `src/obs/`; production time window not measured | Partial |
| ARCH-13-2 | 95% of runs complete or partially complete within the p95 target | ARCH 2.3, 13.1 | run metrics and alert definitions; production sample absent | Partial |
| ARCH-13-3 | 95% of daily-probe alerts arrive within 24 hours | ARCH 2.3, 13.1 | alert freshness telemetry and tests; live sink absent | Partial |
| ARCH-13-4 | Prompt-cache read ratio is at least 70% on cacheable input | ARCH 13.1 | metric catalog exists; vendor/cache measurements are not configured | Blocked-by-external |
| ARCH-13-5 | Isolation violations are zero and any canary leak pages | ARCH 13.1 | isolation suite, canary scan, alert catalog; full suite pending | Partial |
| ARCH-13-6 | Checkpoint resume succeeds 99.9% of the time | ARCH 13.1 | resume tests exist; production sample absent | Partial |
| ARCH-13-7 | Logs use field allowlists and never contain prompts, document text, or tokens | ARCH 13.2 | `src/obs/`, `scripts/canary_scan.py`; canary tests | Partial |
| ARCH-13-8 | Run traces link graph nodes and tool/model calls without tenant metric cardinality | ARCH 13.2 | `src/obs/`; telemetry contract tests | Partial |
| ARCH-13-9 | Every page has a runbook and quarterly chaos/DR exercises are recorded | ARCH 13.4 | `docs/runbooks/`, `docs/oncall/`; cloud game-day is external | Partial |
