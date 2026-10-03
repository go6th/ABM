# ADR-0017: run on Google Cloud instead of AWS (owner override; conflict row C11)

- Status: Accepted (owner instruction, integration prompt 2026-10-02)
- Date: 2026-10-03
- Deciders: product owner

## Context

The skill's architecture (ARCH v2) targets AWS (Aurora PostgreSQL, S3, Secrets Manager, Inngest
as the outer loop). The owner instructed the integration to run on Google Cloud. This is a
conflict between a source document and an owner instruction, recorded as **C11** in
`conflicts.md`.

## Decision

| Concern | ARCH v2 (AWS) | This project (Google Cloud) | Code |
|---|---|---|---|
| Compute | containers | Cloud Run services `api` and `worker`, one image | `infra/modules/runtime`, `Dockerfile` |
| Database | Aurora PostgreSQL | Cloud SQL for PostgreSQL 16, private IP, IAM auth, PITR, CMEK | `infra/modules/database`, `platform/gcp/adapters.cloud_sql_engine` |
| Raw payloads | S3 | Cloud Storage, private, versioned, CMEK | `GcsPayloadStore` |
| Secrets, keys | Secrets Manager, KMS | Secret Manager, Cloud KMS | `SecretManagerSecrets`, `infra/modules/secrets` |
| Outer loop | Inngest | Cloud Tasks behind the `RunScheduler` port (D-18) | `CloudTasksScheduler`, `/tasks/*` |
| LLM | Anthropic API | `LlmPort`: Claude on Vertex AI or the Anthropic API (D-19) | `platform/llm.py` |
| Images, CI auth | ECR | Artifact Registry, Workload Identity Federation | `infra/modules/registry`, `identity` |
| Telemetry | | OpenTelemetry to Cloud Trace and Cloud Logging | `opentelemetry-exporter-gcp-trace` |
| Network | | VPC, direct VPC egress, Cloud NAT fixed egress IP | `infra/modules/network` |
| Rate-limit store | Redis | Memorystore for Redis | `infra/modules/cache` |

Everything that is not a cloud adapter is unchanged: isolation is still enforced by PostgreSQL
RLS, the LangGraph checkpoint is still the source of truth, money is still a ledger.

## Consequences

- Inngest, boto3 and the S3 store are removed from the runtime (commit 4a2fb62, bf052e9).
- The isolation guarantees do not depend on the cloud: the same migrations and RLS run on Cloud
  SQL. VERIFY(V-SQL-1) covers IAM database authentication on the real instance.
- Region availability of every service and model is VERIFY(V-REGION-1) until checked in the
  owner's project.
