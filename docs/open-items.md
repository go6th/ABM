# Open items

Everything below is unverified or undecided. Nothing in this list is claimed to work.

## VERIFY items (cloud and provider facts not checked against the real service)

| ID | What | Where | How to close |
|---|---|---|---|
| V-REGION-1 | Cloud Run, Cloud SQL, Cloud Tasks, Memorystore and the chosen Claude models are available in asia-south1 | infra/envs, ADR-0020 | check in the owner's project before apply |
| V-RUN-1 | Cloud Run deterministic URL form `https://SERVICE-PROJECT_NUMBER.REGION.run.app` used for WORKER_URL | infra/envs/main.tf.tmpl | compare with `terraform output worker_url` after apply |
| V-TASKS-1 | Named-task de-duplication window and dispatch deadline for a 240 s slice on the real queue | platform/gcp/adapters.py, ADR-0018 | staging run of the kill/resume spike |
| V-SQL-1 | IAM database authentication and private-IP reachability through the connector | platform/gcp/adapters.py, runbooks/database.md | staging `/readyz` 200 |
| V-SQL-2 | The checkpointer's psycopg DSN over the Cloud SQL socket (`checkpoint-database-url`) | runbooks/secrets.md | staging run of one slice |
| V-REDIS-1 | Memorystore in-transit encryption: the client needs the instance server CA | app/container.py build_connectors | staging rate-limit test |
| V-LLM-2 | Claude model id format on Vertex AI and enabled models in the region | platform/llm.py, config/models.yaml | one call from staging with the chosen ids |
| V-JOBS-1 | Whether the job feed's description is complete (today treated as unknown, so escalated) | jobs/adapters.py | a recorded sample (docs/providers) |
| V-JOBS-2 | Real PostgreSQL behaviour of the 0013 rollback under concurrent pass writers (the down path drops `pass_no` columns) | migrations/0013 | run `alembic downgrade 0012` on a staging copy with live rows |
| V-PEOPLE-1 | The title-search record shape (employer, current flag, headline) and whether a provider cursor can be exposed to the caller | people/adapters.py, people/models.py RawPerson | one recorded sample per people operation |
| V-PEOPLE-2 | The full-profile record shape (prior employers, dates and their precision, certifications, own description, last activity) | people/adapters.py UnavailableProfiles | a recorded profile sample and the grid contract |
| V-PEOPLE-3 | A posts and comments operation and its record shape (post URL, date, text, last activity) | people/adapters.py UnavailablePosts | a sourced operation and a recorded sample |
| V7 | No provider credit-to-USD rate for the account's plan: credit-priced calls are held at the labelled $0.25 placeholder (`unknown_cost_reserve_usd`) | config/providers.yaml, config/spend.yaml | the plan's credit price from each provider, with a source |
| V-SPEND-1 | Whether each provider offers a free balance endpoint (today every balance reads `unknown`; no provider is excluded for it unless the snapshot says so) | spend/balances.py, BalancePort | a sourced endpoint per provider, or a supplied balance file |
| V-SPEND-2 | Real PostgreSQL concurrency on the target database (Cloud SQL): 200 parallel calls, two workers and ten runs were tested on local PostgreSQL 16 only | connectors/budget.py | the concurrency tests against staging |
| V-EXP-1 | The Excel cell limit of 32,767 characters (taken from Microsoft's "Excel specifications and limits"; not checked against Excel itself) | config/export_rules.yaml limits | open a workbook with a 32,767 and a 32,768 character cell in Excel |
| V-EXP-2 | LibreOffice recalculates openpyxl formulas on load and caches results (verified locally with LibreOffice 26.2.4.2 by `test_integration_checks.py`); the production image has no LibreOffice | exports/verify.py | install it in the gate image and re-run the integration checks there |
| V-EXP-3 | Package normalisation holds for future openpyxl and python-docx versions (verified for openpyxl 3.1.5 and python-docx 1.2.0 by the byte-identical rebuild test) | exports/ooxml.py | the determinism test on every dependency upgrade |
| V-LG-1 | LangGraph 1.2.12 keeps completed sibling `Send` task writes when the superstep is cancelled and re-runs only the unfinished task on resume: **verified** with `InMemorySaver` (scratch experiment, 2026-10-06); not verified with the Postgres saver. The parallel design does not depend on it (unit events are the truth, ADR-0037) | workflow/parallel_phase.py | repeat the experiment with `AsyncPostgresSaver` |
| V-GOV-1 | Changing function ownership to the NOLOGIN role `governor_definer` when the production migrator is not a superuser (PostgreSQL 16 `createrole_self_grant`, ADMIN option) | migration 0017 | run migration 0017 as the Cloud SQL migrator role on staging |
| V-TASKS-2 | Cloud Tasks queue `maxConcurrentDispatches` as a coarse backstop for slices; the governor stays authoritative | infra/modules/tasks | read the queue config on staging and document the chosen value |
| V-UPL-1 | Signed upload URLs on Cloud Storage with a size condition (V4 signing, `x-goog-content-length-range`) into the quarantine prefix | spend/upload.py UploadStore | implement the adapter and test it against the emulator and staging |
| V-UPL-2 | A malware scanning option for uploaded files (no scanner is chosen; the placeholder fails closed) | spend/upload.py ScanPort | product owner choice, then an adapter test |
| V-AUTH-1 | The identity provider's step-up claims (`amr` containing `mfa`, integer `auth_time`) | api/auth.py step_up | read the IdP's token documentation; one staging token |
| V-LLM-3 | The exact 400 error wording for an over-long prompt (mapped to `ContextTooLong` when the message says "too long") and per-model `cache_control` support (V2) | llm/adapters/anthropic_api.py | one staging call per candidate |
| V-LLM-4 | Context windows, prices, zero-retention terms and caching support of every candidate (all null today) | config/model_routing.yaml | the provider's dated documentation, recorded as each fact's source |
| V-CI-1 | Pin `actions/upload-artifact` in eval.yml to a verified commit sha (the tag is used until then) | .github/workflows/eval.yml | look up the release commit on the action's repository |
| V-CI-2 | Required-check behaviour: making `eval` a required status check in branch protection | docs/evals/ci-and-budget.md | repository settings, once real golden labels exist |
| V-OBS-1 | OTLP or Cloud Trace exporter package and its configuration (not installed; `obs.exporters.otlp_exporter` refuses) | src/obs/exporters.py | install, then a staging trace |
| V-OBS-2 | Google's evaluation of our alert semantics (the generated JSON passes `terraform validate` with hashicorp/google ~> 6.0; the simulator in `obs.alerts` is ours, not Google's evaluator) | infra/modules/observability | a staging alert test |
| p_* | Provider facts tagged in docs/providers/CLAIMS.md | connectors/providers | per CLAIMS.md |

## Open items (behaviour decisions)

| ID | Item | Default today |
|---|---|---|
| OI-LLM-1 | anthropic 1.11.0 `messages.create` has no `temperature`; fixed temperature cannot be set | schema validation and one re-ask |
| OI-TRK-1 | The verifier may report only `lead_verification`; "rows holding Leads" needs a Lead-to-row map | verifier rows from config |
| OI-TRK-2 | A run with no Leads cannot report `lead_verification` as Done or None (no calls to cite) | Not done `not_planned`: such runs end PARTIAL |
| OI-JOBS-1 | `job_postings_read_classified` is derived from ledger counts, so "nothing matched" is Done, not the spec's None | tracker derivation |
| OI-JOBS-2 | The live board search is a web search restricted to the careers host (no keyword job-board operation) | web_search per keyword |
| OI-RUN-1 | `runs.status` cannot be widened expand-only; non-COMPLETED ends map to `failed` with the state in `error` (ADR-0015) | mapping |
| OI-BLD-1 | No deliverable builder or production quality gate exists: every run ends PARTIAL `builders_not_configured` | NotConfigured |
| OI-PPL-1 | p_people unsupported until the grid contract is supplied; people stage uses p_org | config/people_sources.yaml |
| OI-PPL-2 | The tracker accepts only this run's calls as proof, so a run that reuses every profile from an earlier run reports `full_profiles_senior_people` Not done `not_planned` | honest Not done; needs a "reused from run X" proof kind |
| OI-PPL-3 | No earlier stage stores people named in research as structured data, so the mention step has no input in production | `NoMentions` (NotConfigured) |
| OI-PPL-4 | Association search needs a technology identifier per lane (`association.technology_by_lane`) | lanes end `not_available_yet` |
| OI-PPL-5 | Migration 0014 has `down_revision` 0012 because 0013 is uncommitted on `feat/job-posting-pipeline`; re-point it when the branches meet | revision 0014 |

## Decisions needing the owner (register: docs/decisions/open-decisions.md)

D-01 ext, D-04 (settlement rule, ADR-0016), D-06, D-10, D-11 ext, D-12, D-13, D-14, D-15, D-16,
D-17, D-18, D-19, D-20, D-21, D-22, D-23, the judge model profile (the active arm routes judge and generator to one
model).

## Credit cap (open items and follow-ups)

| ID | Item | Default today |
|---|---|---|
| OI-SPEND-1 | Provider cost table: feed prices per call, credit rates (V7), profile enrichment cost | placeholders labelled in config; unknown costs need an approved sample line |
| OI-SPEND-2 | Approver roles, approval and extension timeouts, headroom, rounding step, extension limit, variance threshold, snapshot age | `config/spend.yaml` proposals |
| OI-SPEND-3 | Tenant daily cap and monthly refresh budgets (`CapPolicyPort` hook only) | not built |
| OI-SPEND-4 | Wallet pricing and top-ups (D-04) | settlement commits `reserved * min(1, spent / cap)` (ADR-0016) |
| OI-SPEND-5 | Operator console screens for proposals, approvals, extensions, pre-pulled uploads and the report | API routes only |
| OI-SPEND-6 | Notifier adapter (operator prompt) in production | `None`: prompts are not sent; the run still waits in AWAITING_APPROVAL |
| OI-SPEND-7 | Tenant-level `allow_fallback_on_exhaustion` and `auto_approve_within_reserve` have no settings API | set in `tenants.policy_json` |
| OI-SPEND-8 | Replays and cache hits write no ledger event, so the report shows them as not measured; "avoided spend" is not estimated yet | `not measured` |
| OI-SPEND-9 | The K10 budget node ranks within one stage wave; a cross-stage plan rank needs the planner to emit the whole plan first | per wave |
| OI-SPEND-10 | The nightly invariant job needs its Cloud Scheduler and Cloud Run job resources in `infra/` | entry point `spend.jobs:main` |
## Export builders (open items and follow-ups)

| ID | Item | Default today |
|---|---|---|
| OI-EXP-1 | Distill does not write `report_sections` yet (first follow-up) | the Summary is refused (`missing_narrative`); the run ends PARTIAL `build_failed` |
| OI-EXP-2 | The jobs stage does not register posting links in the source registry | posting rows fail B4 (`missing_source_code`); the Postgres test registers them to prove the rest |
| OI-EXP-3 | The people stage does not register Findings post links | findings fail B4; the Postgres test registers them |
| OI-EXP-4 | Grading's `record_graded_fact` does not write `facts.lane` | facts render under "Findings not assigned to a lane" |
| OI-EXP-5 | No pipeline stores team data | the Teams sheet carries the visible "None recorded" note |
| OI-EXP-6 | `tech_detections` has no writer; its `source_ref` must be a registered URL | detections without a URL fail B4 |
| OI-EXP-7 | `tenants.policy_json.salesforce_partner` has no settings API | false unless set in the database |
| OI-EXP-8 | Client staff names come from the Phase B redaction map, which does not exist | only other accounts' names and domains and guarded people names are forbidden terms |
| OI-EXP-9 | Cloud Storage put-if-absent uses get then put (a race writes the same content-addressed bytes); an `if_generation_match=0` precondition is better | content addressing makes the race harmless |
| OI-EXP-10 | An object put before a killed commit is unreferenced until the retry reuses it; a sweeper for unreferenced objects | never served; reused on retry |
| OI-EXP-11 | Download links and roles for deliverables (API) | none |
| OI-EXP-12 | The legacy Node library, workbook scripts and one real delivered file set (port, characterization tests, legacy parity) | Blocked; neutral proposal theme |
| OI-EXP-13 | Stale-phrase list, abbreviation list, more partner framings, RESTORE.md wording, news attribute prefixes | empty or placeholder, `source: TODO` |
| OI-EXP-14 | `config/output_spec.yaml` comments name the Internal Only file after a person; the skill names it "Internal Only" | the builders never build it; rename with the owner |

## Phase 3 (tenant product) VERIFY items

| ID | What | Where | How to close |
|---|---|---|---|
| V-P3-IDP-1 | Real OIDC providers' token endpoint authentication, `amr` and `auth_time` claims, key rotation cadence | `src/identity/oidc.py`, `ports.py` | one sign-in per supported IdP in staging |
| V-P3-IDP-2 | Real SAML metadata, signing algorithm and certificate rollover of each customer IdP | `src/identity/saml.py` | the customer's metadata and a staging sign-in |
| V-P3-IDP-3 | The 6th staff IdP and its claim names (`staff_role`, `amr`, `auth_time`) | `src/account_intelligence/api/operator.py` | staff IdP configuration |
| V-P3-COOKIE-1 | `__Host-` prefix and `SameSite` behaviour in the supported browsers, including the SAML POST back | `config/session.yaml` | Playwright run on the deployed origin |
| V-P3-EGRESS-1 | IdP and connector calls through the platform egress proxy (ARCH 11) | `src/identity/ports.py` | staging egress test |
| V-P3-CN-1 | Google Drive narrowest scope for admin-named folders, OAuth verification requirements, shortcut and shared-drive ancestry behaviour | `config/connectors.yaml`, `src/connectors_client/drive.py` | Google's current docs and a test project |
| V-P3-CN-2 | Salesforce OAuth flow, `api` scope breadth and refresh-token policy | `config/connectors.yaml` | Salesforce docs and a developer org |
| V-P3-CN-3 | HubSpot scopes `crm.objects.companies.read` and `crm.objects.deals.read` and the authorize endpoint | `config/connectors.yaml` | HubSpot docs and a developer account |
| V-P3-SSE-1 | Server-sent events behind Cloud Run (buffering, request timeout, heartbeat) | `src/account_intelligence/api/routes/runs.py` | staging stream of a 20-minute run |
| V-P3-GCS-1 | Signed URL and CMEK behaviour for tenant object paths | `src/tenancy/storage.py` | staging object store adapter |

## Phase 3 open items

| ID | Item | State |
|---|---|---|
| OI-P3-1 | The Word and PDF header carries the branding display name, not yet the logo and accent colour | follow-up |
| OI-P3-2 | First-call questions are not shown in the client app (needs a sixth materialised table kind, which alters a CHECK constraint): usefulness events for questions arrive only through the API | follow-up, needs an owner decision on the constraint change |
| OI-P3-3 | `refresh_run` usage events are never emitted: no refresh endpoint exists | follow-up |
| OI-P3-4 | Client-history routing from connectors into runs is partial (quarantine only; parsing is Phase B) | Phase B |
| OI-P3-5 | Configure the Google Workspace OAuth web client, exact redirect URI, Workspace domain, designated operator email allowlist entry, and enabled auth_time/amr claims; the staff sign-in and connected console are implemented | deployment |
| OI-P3-6 | Audit archive with Object Lock (7 years) not built | Blocked |
| OI-P3-7 | Commits 5c32e44 and 4d5e1ec on `feat/phase3-tenant-product` also contain another session's uncommitted RAG work (`src/account_intelligence/rag/`, `tests/rag/`) | needs the owner's decision |

## Phase 4 (automation and signals) VERIFY items

| ID | What | Where | How to close |
|---|---|---|---|
| V-P4-MON-1 | Which providers offer monitoring (change detection, webhooks) per monitor kind, their signature schemes and replay rules | `src/signals/registry.py`, `src/signals/webhooks.py` | provider docs and a test account per provider |
| V-P4-SLACK-1 | Slack app scopes for posting to a user, rate limits and idempotency | `src/alerts/channels` | Slack app registration and docs |
| V-P4-CRM-1 | Salesforce and HubSpot create endpoints for notes, tasks and a custom snapshot object; sandbox behaviour; search by marker | `src/crm_writeback/salesforce.py`, `hubspot.py` | a Salesforce sandbox and a HubSpot developer account |
| V-P4-SCHED-1 | Cloud Scheduler and Cloud Tasks semantics for the tick (at-least-once, deduplication window) | `src/refresh/slots.py` | staging tick test |
| V-P4-SQL-1 | Cloud SQL backups, PITR window and restore time on the chosen tier | `docs/phase4/dr_plan.md` | staging restore game-day |
| V-P4-SQL-2 | Cross-region replica lag and promotion | `docs/runbooks/regional-failover.md` | staging failover game-day |
| V-P4-K6-1 | k6 options and thresholds for the staging profile | `ops/load/k6_profile.js` | k6 docs and a staging run |

## Phase 4 open items

| ID | Item | State |
|---|---|---|
| OI-P4-1 | The probe's grading port is wired to fakes; production wiring to the workflow's tool-less extractor and grading nodes is not built | Partial |
| OI-P4-2 | A refreshed Summary built through the export builders (diff-first section in the Word file, the gate and Phase 3 parity on the refreshed package) is not built; the diff section renders and lints | Partial |
| OI-P4-3 | A crashed probe's polls are not reused by its retry (the retry polls again and reserves again); facts and alerts are not duplicated | Partial |
| OI-P4-4 | Inbound webhook payloads are stored as quarantine references; the hand-off to the extractor is not wired | Partial |
| OI-P4-5 | Real monitor adapters, Slack, email and CRM adapters raise NotConfigured | Blocked on registrations |
| OI-P4-6 | Operator views for monitoring cost and throttle state exist as functions and SQL, not as operator routes or pages | follow-up |
| OI-P4-7 | HubSpot adapter has the same shape as Salesforce; neither is built against a real API | Blocked |

## ABM agent layer (2026-10-08)

| Id | Item | State |
|---|---|---|
| OI-ABM-1 | The ABM graph runs under the production runner (`execute_slice` graph factory) for Phase 1 only and Phase 2 only runs and, for full runs, behind the `abm_agents` tenant flag; making it the default for every tenant is the owner's decision (C34) | done behind flag |
| OI-ABM-2 | [VERIFY] lessons.md format (`## YYYY-MM-DD` headings, bullets) against the archive | Blocked on archive |
| OI-ABM-3 | Archive absent: checklist reconciliation (C31), lessons, prompt text from runbooks, sense-check questions, consent text, signal tiers | Blocked on archive |
| OI-ABM-4 | Production bindings done (`src/agents/production/`, ADR-0100) except: people recording from the agent layer (NotConfigured, counted), the internal-history connector search (D-98 keeps it off), the Internal Only file builder (the internal audience builds no file), the export builders reading `abm_distill_outputs` | partial |
| OI-ABM-8 | Grading attribute classes cover leadership, hiring, technology and funding only; most coverage rows' claims are refused `unknown_attribute_class` (C38) | needs owner (half-lives) |
| OI-ABM-5 | LangGraph APIs used: `interrupt`, `Command(resume=...)`, conditional edges, `InMemorySaver` (verified against installed langgraph 1.2.12 by tests). `Send`, subgraphs and per-node model binding are not used (routing is by task kind) | verified |
| OI-ABM-6 | Mutation testing of the guard modules: see BUILD_LOG (ABM section) | see log |
| OI-ABM-7 | Budgets in `config/agents.yaml` are unmeasured proposals | needs golden accounts |
