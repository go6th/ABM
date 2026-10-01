# Decision log: Phase 0

Source: roadmap section 6 (Phase 0 work items and deliverables), section 12 (technology choices) and section 16 (open decisions).

Every decision below is **Proposed**. A decision becomes **Decided** only when all three sign-off lines are filled in. Evidence slots are written by the benchmark scripts (`app/decision_log.py`), which refuse to write results from fake providers, recorded doubles or synthetic golden accounts unless explicitly told to, and then stamp them NOT EVIDENCE.

How to fill an evidence slot with real results:

```
python -m app.benchmarks.model_split --live --decision-log docs/decision_log.md
python -m app.benchmarks.people_source --decision-log docs/decision_log.md
```

Both need real, analyst-reviewed golden accounts in `tests/evals/golden/` first. The model split also needs `ANTHROPIC_API_KEY` and the Phase 1 provider adapter; the people source run needs live recordings captured with `BITSCALE_API_KEY` and `SUMBLE_API_KEY`.

---

## D1. Workflow engine

**Status:** Proposed

**Question.** What runs each account as a durable workflow (stages, dependencies, retries, timeouts, pause for approval, resume after failure)?

**Options**

| Option | Summary |
|---|---|
| A. Temporal | Durable execution, retries, timeouts and schedules built in. Agent steps run as activities. |
| B. LangGraph on a job queue | Graph-shaped agent reasoning with checkpoints; queue, retries and schedules supplied separately. |
| C. Managed workflow service | Cloud provider workflow service; ties orchestration to the cloud choice. |

**Comparison criteria** (each scored 1 to 5 by the tech lead, evidence in the slot below)

| Criterion | Why it matters (roadmap reference) |
|---|---|
| Durable resume after a crash without repeating paid pulls | s8 idempotency and resume; s3 principle 2 |
| Pause for approval and resume on one click | s8 credit manager v2 |
| Parallel fan-out with per-broker concurrency limits | s8 parallel execution |
| Schedules for refresh and monitors | s10 refresh engine and signal monitors |
| Workflow history per run for audit | s8, s11.4 audit |
| Local developer experience and testability | s6 environments and CI |
| Operating cost and hosting options (self-hosted or managed) | s12 infrastructure |
| Fit with Python agents and connectors | s12 API row |

**Recommendation.** Temporal for orchestration, with agent steps as activities, and LangGraph only inside a single activity if graph-shaped reasoning is needed. This is the roadmap's recommended default (section 6).

**Context from the earlier design.** The older architecture documents in this repository chose LangGraph checkpoints plus an Inngest outer loop (`docs/adr/ADR-002-langgraph-checkpoint.md`, `docs/adr/ADR-003-inngest-outer-loop.md`). If D1 is signed as Temporal, those two records are superseded and should be marked so.

**Evidence**

<!-- BEGIN EVIDENCE: workflow_engine -->
Phase 0 skeleton evidence only, not a comparison:

- A Temporal hello-world workflow with one activity runs in tests through the Temporal time-skipping test server (`tests/app/test_workflow.py`) and locally through docker compose (`make temporal-up`, `make worker`, `make hello`).
- No LangGraph or managed-service spike has been run. The tech lead fills the criteria scores above from a short spike or documented experience.
<!-- END EVIDENCE: workflow_engine -->

**Sign-off**

| Role | Name | Date | Decision |
|---|---|---|---|
| Tech lead | | | |
| Analyst owner | | | |
| Product owner | | | |

---

## D2. Model split

**Status:** Proposed

**Question.** One model for the whole run, or a faster model for pulls and extraction and a stronger model for analysis and deliverables?

**Options**

| Option | Summary | Config |
|---|---|---|
| A. Single model | One model for every task (today's setup) | `config/models.yaml` arm `single` |
| B. Split by task | Fast model for pull planning, extraction and classification; strong model for synthesis, narrative and verification | `config/models.yaml` arm `split` |

**Method.** Run the same task set through both arms on three of the September accounts. Compare coverage, citation validity, cost and time (roadmap section 6). Harness: `app/benchmarks/model_split.py`.

**Recommendation.** Split by task, confirmed on the Phase 0 benchmark (roadmap section 16). The choice stays reversible: runs read the active arm from `config/models.yaml`, so switching is a config change with a version bump.

**Evidence**

<!-- BEGIN EVIDENCE: model_split -->
No evidence yet. The harness has only run offline with a fake provider on synthetic accounts, which says nothing about real models.
<!-- END EVIDENCE: model_split -->

**Sign-off**

| Role | Name | Date | Decision |
|---|---|---|---|
| Tech lead | | | |
| Analyst owner | | | |
| Product owner | | | |

---

## D3. People source

**Status:** Proposed

INTERNAL: this decision names data brokers and must not be copied into client-facing material.

**Question.** Does BitScale alone cover people search, or does Sumble stay in the people mix? Sumble stays for organizations, tech stack and teams either way.

**Options**

| Option | Summary |
|---|---|
| A. BitScale alone | Title search on the domain; two seniority passes |
| B. BitScale plus Sumble | Adds people by technology association to fill gaps |

**Method.** Run both options on the same three accounts. Compare people found, overlap, people added by Sumble and credits per added person. Harness: `app/benchmarks/people_source.py`. People are merged by profile URL then name, with the exclusions in `config/people_scope.yaml`.

**Recommendation.** Decide on the side-by-side result (roadmap section 16). Keep the decision reversible through `config/routing.yaml`.

**Evidence**

<!-- BEGIN EVIDENCE: people_source -->
No evidence yet. The harness has only run on synthetic recorded responses.
<!-- END EVIDENCE: people_source -->

**Sign-off**

| Role | Name | Date | Decision |
|---|---|---|---|
| Tech lead | | | |
| Analyst owner | | | |
| Product owner | | | |

---

## Other Phase 0 decisions still open (roadmap section 16)

| Decision | Recommendation in the roadmap | Decide by | Where it lands |
|---|---|---|---|
| Hosting region | Single region first, designed for a regional split | Week 2 | `infra/terraform/envs/*/terraform.tfvars` (`region`) |
| Cloud provider | Not named in the roadmap; needed for infrastructure as code | Week 2 | `infra/terraform/envs/*/terraform.tfvars` (`cloud_provider`) |
