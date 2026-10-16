# `src/`: the application code

Everything that runs in the api and worker containers lives here, as a set of Python packages
that import each other in one direction only (lower layers never import higher ones; the
contracts are in `pyproject.toml` under `[tool.importlinter]` and checked by `make static`).
Every module starts with a docstring that says what it is for (100% docstring coverage is a
gate), so the fastest way to learn a package is to read its `__init__.py` and the first lines of
each file.

## The application package

`account_intelligence/` is the application proper. Its subpackages, in the order a request
meets them:

| Subpackage | What it is |
| --- | --- |
| `api/` | FastAPI: `main.py` (ASGI entry, startup wiring, SIGTERM handling), `routes/` (one module per screen or resource; `routes/__init__.py` mounts them under `/v1`, `/operator/v1` and `/tasks`), `policy.py` (the default-deny route catalog), `guard.py`, `auth.py`, `contract.py` (the typed response models the OpenAPI snapshot is built from), `health.py` probes. |
| `app/` | `container.py`: the single place that chooses adapters for the current `APP_ENV` (hermetic for dev and tests, Google Cloud for staging and production). |
| `db/` | `session.py` (`tenant_txn()`: the only way to open a tenant transaction; sets the RLS tenant), ORM models, owners, enums. |
| `platform/` | Ports (object store, task queue, model, mail…) and their adapters: `hermetic.py` for local and tests, `gcp/` for the cloud. |
| `connectors/` | The provider broker layer: provider codes, routing, cost, the research cache, hermetic cassettes. Vendor names live only in `config/providers.yaml`. |
| `grading/` | The grading engine: facts, evidence, engine grade, product grade, the primary-source rule, lead rule. Pure rules plus the one fact write path. |
| `tracker/` | The 33 coverage rows (the research checklist) as a state machine with proof. |
| `workflow/` | The durable research workflow: `runner.py` (slices, checkpoints, finish), `engine.py`, stages, the spec loaded from `config/workflow_v1.yaml`. |
| `runs/` | Run lifecycle: create (one transaction with the credit reservation), transitions, settlement, the sweeper. |
| `worker/` | `python -m account_intelligence.worker`: one run per container, and the nightly `spend-invariants` and `access-expiry` checks. |
| `replica/` | The product screens' server side: the Account workspace (`workspace.py`), the deep-dive sheets and their Excel (`sheets.py`), Ask answers, research jobs, credits, schedules, the 6th Library, downloads, limits, isolation checks. The name is historical (it was built as a working replica of the design prototype); this is the live product. |
| `chat/` | Tenant-private chat threads and the allowlisted model registry. |
| `rag/` | Retrieval over uploaded documents: hybrid search, ACL filters, prompts, guardrails. |
| `safety/` | The one client-output scanner every export path calls (no vendor, credit or person names in a client file). |
| `agents/` (inside this package) | The grading facade and versioned prompts used by Workflow v1. The ABM graph is the top-level `agents/` package below. |
| `config.py` | The typed environment registry (every variable the code reads; `docs/ENVIRONMENT.md` is generated from it). `config_files.py` loads and fingerprints the YAML rules. `dev_limits.py`, `model_routing.py`, `urls.py` are small helpers. |

## The ABM research graph

| Package | What it is |
| --- | --- |
| `agents/` | The eight-step ABM research run as bounded LangGraph agents: `graph.py` (setup → cap_approval → pull → interpret → sense_check → targeted_pulls → distill → build → gate → deliver → finish), one folder per agent (`setup/`, `pull/`, `pass1/`, `pass2/`, `people/`, `sense_check/`, `distill/`, each with its `prompt.md`), `runtime.py` (budget reservation before any paid call, output guards), `guards.py`, `schemas.py`, `skill_context.py` (the owner's research skill from `config/skill/archive/` as system context), `production/` (the wiring into the platform: records, provider routes, grading, state projection). |

## Deliverables and pipelines

| Package | What it is | Used by |
| --- | --- | --- |
| `exports/` | The standing file set from one snapshot: Word (summary, sources and evidence), Excel (people, jobs, tech and teams), the workspace zip; rules from `config/export_rules.yaml`, theme from `config/export_theme.yaml`; the safety gate. | graph `build` step, downloads, views |
| `documents/` | Uploaded documents as research input: upload sessions, quarantine, parsing in a sandbox, redaction, chunking, embedding, indexing, the file research graph, and `deliverables.py` (an uploaded Job Postings, People List or Tech Stack workbook becomes the account's job postings, people and technologies). | api `documents` routes, worker task `document-process` |
| `research_files/` | The parsers, blob store and limits the documents pipeline uses (`config/research_files.yaml`). | `documents/` |
| `devstore/` | The development object store (resumable uploads on a directory). Dev and test only; the cloud uses GCS. | compose.replica.yml `objectstore` |
| `jobs/` | Job postings pipeline: classify, dedupe, gap passes, counts. | agents, exports, people |
| `people/` | People pipeline: titles, merge, scope, profiles, counts. | agents, exports, tenancy |
| `signals/` | Signal monitors: typed outputs, fingerprints, clustering, significance. | alerts, refresh |
| `refresh/` | The refresh engine: shelf life, slots, probe then escalate, deltas, history. | replica schedules, alerts, signals |
| `alerts/` | Alert rules, subscriptions, caps, templates, the outbox and channels. | refresh, crm_writeback |

## Money, tenants, operators

| Package | What it is |
| --- | --- |
| `spend/` | Credit cap and spend rules: money model, gate, approval, priority, reconcile, the end-of-run report. Every rule lives here; the API routes are thin. |
| `metering/` | Insert-only usage events, atomic plan limits (`config/plans.yaml`), the billing-ready period summary. |
| `governance/` | Monitoring cost governance: the monthly allowance and the throttle ladder. |
| `unitcost/` | Unit cost roll-ups per account (broker and model cost) and the anomaly alert. |
| `tenancy/` | Tenant settings, per-tenant storage paths, registries, the tenant lifecycle (suspend, delete). |
| `identity/` | Sessions, CSRF, roles, membership rules, SSO (OIDC and SAML), the local password issuer, rate limits. |
| `admin/` | The operator plane: tenants, quotas, flags, support grants, view-as. |
| `audit/` | The insert-only, hash-chained audit record and its verification. |
| `views/` | Typed row sources for the client tables, the bounded query layer, saved views, branding, safe rendering. |
| `connectors_client/` | The client's own mail and drive connections (narrow OAuth scopes). |
| `pilot/` | Pilot feedback and the usefulness metric. |
| `crm_writeback/` | Create-only, opt-in CRM write-back. |
| `opendoor/` | The versioned signal event contract, read API and signed webhooks. |

## Models, quality, observability

| Package | What it is |
| --- | --- |
| `llm/` | Model routing: task kind → tier → candidate ladder from `config/model_routing.yaml`; the reply cache. The only entry to a language model. |
| `verify/` | Verifier agents: blind re-reads whose only authority is to downgrade. |
| `evalgate/` | The eval harness (metrics, golden sets, contamination check, benchmark CLI). |
| `parallel/` | The dependency-aware unit scheduler with per-provider concurrency limits. |
| `obs/` | The telemetry contract (metrics, spans, log fields from `config/telemetry/`), the Cloud Monitoring export, alert and dashboard rendering, cost roll-ups, SLOs. |

## Roadmap section 11: conformance as code

`crosscut/` evaluates `config/section11_spec.yaml` against the repository (every requirement
names its evidence: a symbol, a test, a column or a config file) and writes
`docs/crosscutting/CONFORMANCE.md`. The small packages it points at hold the implementation:
`datamodel/` (the data model spec), `contracts/` (contract drift), `quality/` (regression policy,
analyst loop), `privacy/` (minimisation, retention, DSAR), `security/` (secret scan, rotation,
SOC 2 coverage, pentest gate, review store), `metrics/` (success metrics report, link check),
`registers/` (risk, decision, claim and messaging registers).
