# `config/`: the rules the code reads as data

Every YAML file here is loaded by exactly the module named in the table (nothing globs the
folder except the fingerprint in `account_intelligence/config_files.py`, which hashes every
`*.yaml` into the run's `config_version`). Each file starts with a comment that names its
source document and says whether it is a sourced rule or a proposal. The api and worker images
copy this folder; the local compose stack mounts it read-only, so an edit is live on the next
request without a rebuild.

## Research and grading

| File | What it decides | Loaded by |
| --- | --- | --- |
| `agents.yaml` | The agent registry: one agent per step, its model task kind, token and call budgets, retries | `agents/config.py` |
| `skill/source.yaml` | Which version of the owner's research skill is encoded (1.11) and that its archive is present | `agents/config.py` |
| `skill/archive/` | The owner's research skill itself (SKILL.md and references), scrubbed of tool and person names; put into every ABM model call as system context | `agents/skill_context.py` |
| `skill/run.yaml`, `skill/pull_plan.yaml`, `skill/provider_routes.yaml` | The shape of a run, which step pulls which coverage row, how route needs map to providers | `agents/config.py`, `agents/production/provider.py` |
| `skill/consent.yaml`, `skill/conversions.yaml` | People-data consent rules; operator-in-the-loop behaviours turned into settings | `agents/config.py` |
| `skill/name_digests.yaml` | Digests of names that may never appear anywhere (scanned, never spelled out) | `scripts/abm_name_scan.py`, `agents/naming.py` |
| `skill_versions.yaml` | Skill versioning: a run pins its config version | admin console, `agents/config.py` |
| `lessons.yaml` | Lessons as code: every lesson from the skill archive with its test | `agents/lessons.py` |
| `grading.yaml`, `grading_policy.yaml`, `source_registry.yaml`, `routing.yaml` | The grading vocabularies, the policy (what may be stated to a prospect), source tiers, provider routing | `account_intelligence/grading/config.py` |
| `checklist.yaml`, `tracker_v1.yaml` | The 33 coverage rows and the tracker's completion and transition rules | `account_intelligence/tracker/config.py` |
| `workflow_v1.yaml` | Workflow v1: the 13 stages, dependencies, tool categories | `account_intelligence/workflow/spec.py` |
| `research_scopes.yaml`, `research_input_modes.yaml`, `research_cache.yaml`, `research_files.yaml` | Refresh scopes; the two input modes (company, file); what a run may reuse from earlier runs; file parser limits | `replica/jobs.py`, `documents/config.py`, `connectors/cache.py`, `research_files/config.py` |
| `providers.yaml`, `connectors.yaml` | INTERNAL: the data providers (the only place a vendor name may live) and the client connectors | `connectors/settings.py`, `connectors_client/*` |
| `model_routing.yaml`, `models.yaml`, `chat_models.yaml` | Model routing ladder (a change needs a recorded decision), model prices, the public chat catalogue | `llm/*`, `platform/llm.py`, `chat/registry.py` |
| `people_rubric.yaml`, `people_scope.yaml`, `people_sources.yaml`, `posting_rubric.yaml`, `job_lanes.yaml`, `jobs_v1.yaml`, `lanes.yaml` | People and job pipelines: rubrics, scope, lanes, caps | `people/config.py`, `jobs/config.py` |
| `output_spec.yaml`, `export_rules.yaml`, `export_theme.yaml`, `client_output_denylist.txt` | The deliverable set and quality gate, export content rules, the one style table, patterns that may never reach a client file | `exports/config.py`, `safety/client_output.py` |
| `verify.yaml`, `evals.yaml`, `regression.yaml` | Verifier agents, the eval gate thresholds, the regression policy | `verify/config.py`, `evalgate/config.py`, `quality/regression.py` |
| `parallel.yaml` | Unit types, dependencies and fan-out for parallel execution | `parallel/config.py` |
| `rag.yaml` | Retrieval thresholds and caps | `account_intelligence/rag/config.py` |

## Money and limits

| File | What it decides | Loaded by |
| --- | --- | --- |
| `credits.yaml` | Research credits: estimates, the pool, what a run costs the client | `replica/credits.py` |
| `spend.yaml` | Credit cap v1 rules | `spend/config.py` |
| `billing.yaml`, `pricing.yaml` | Settlement rules; the proposed model price catalogue (evidence for the unit-cost requirement) | `config_files.py`; `crosscut` evidence |
| `budgets.yaml` | The monthly monitoring allowance and throttle ladder | `governance/monitoring_budget.py` |
| `plans.yaml`, `tenant_settings.yaml`, `rate_limits.yaml` | Plan limits, tenant setting bounds, per-user money and compute limits | `metering/limits.py`, `tenancy/settings.py`, `identity/rate_limit.py` |

## Tenants, identity, operators

| File | What it decides | Loaded by |
| --- | --- | --- |
| `roles.yaml`, `auth.yaml`, `session.yaml` | Tenant roles, the local password policy, session and download-token policy | `identity/*`, `api/membership.py` |
| `ownership.yaml` | Private workspaces and which module owns (writes) which table | `db/session.py`, `replica/isolation.py`, `tests/system/test_single_write_paths.py` |
| `support.yaml`, `deletion.yaml`, `audit.yaml` | Support access, tenant deletion cascade, audit event classes | `admin/support.py`, `tenancy/lifecycle.py`, `audit/events.py` |
| `views.yaml` | Client table queries, saved views, branding, run progress labels | `views/*`, `api/routes/runs.py` |
| `pilot.yaml` | Pilot feedback and usefulness events | `pilot/feedback.py` |
| `crm.yaml` | CRM write-back mapping and approvals | `crm_writeback/service.py` |

## Refresh, signals, alerts

| File | What it decides | Loaded by |
| --- | --- | --- |
| `refresh.yaml`, `shelf_life.yaml`, `scoring.yaml` | Refresh engine, shelf life per signal type, significance scoring | `refresh/*`, `signals/score.py` |
| `alert_rules.yaml`, `alerts_extra.yaml` | Alert rules and noise control | `alerts/rules.py` |

## Telemetry

| File | What it decides | Loaded by |
| --- | --- | --- |
| `telemetry/metrics.yaml`, `spans.yaml`, `logs.yaml` | The telemetry contract: the only metrics, span names and log fields allowed (a JSON log may carry nothing else) | `obs/catalog.py` |
| `telemetry/alerts.yaml`, `slos.yaml`, `severity.yaml`, `dashboards.yaml` | Alert catalogue, SLOs, severity matrix, dashboards; rendered into Terraform by `scripts/gen_observability.py` | `obs/alerts.py`, `obs/dashboards.py` |

## Roadmap section 11 evidence

`section11_spec.yaml` lists every cross-cutting requirement with its proof. The files it names
exist for that reason and are read by the conformance checker (`src/crosscut/conformance.py`),
not by the running application: `claims.yaml`, `messaging_rules.yaml`, `risk_register.yaml`,
`secrets_rotation.yaml`, `soc2_controls.yaml`, `data_model_spec.yaml`, `drift.yaml`,
`minimisation_map.yaml`, `retention.yaml`, `dsar.yaml`.

## Hermetic fixtures

`hermetic/fake_model/` and `hermetic/providers/` hold the deterministic model replies and the
synthetic provider cassettes the local stack and the tests run on, so no key and no paid call
is ever needed to exercise the whole product.
